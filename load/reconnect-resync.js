// (e) Disconnect/reconnect resync storm: repeated close + fresh ticket + hello.
import { check } from 'k6';
import ws from 'k6/ws';
import { login, metrics, socketUrl, ticket } from './lib/common.js';

const vus = Number(__ENV.RC_VUS || 20);
const duration = __ENV.RC_DURATION || '30s';
const holdMs = Number(__ENV.RC_HOLD_MS || 500);

export const options = {
  scenarios: {
    reconnect: { executor: 'constant-vus', exec: 'reconnect', vus, duration },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

let session;

export function reconnect() {
  if (!session) session = login();
  const clientId = `load-rs-${__VU}-${__ITER}`;
  // Same sessionId across reconnects: a lost connection, not a page reload.
  const sessionId = `${clientId}-session`;
  const value = ticket(session);
  const start = Date.now();

  const res = ws.connect(socketUrl(value, clientId, sessionId), {}, (socket) => {
    socket.on('open', () => metrics.handshakes.add(1));
    socket.on('message', (raw) => {
      metrics.roomMessages.add(1);
      let message;
      try {
        message = JSON.parse(raw);
      } catch {
        return;
      }
      if (message.type === 'presence:update') metrics.presenceUpdates.add(1);
      if (message.type === 'hello') {
        metrics.helloRtt.add(Date.now() - start);
        // hello.members is a map of userId -> presence payload, not an array.
        check(message, {
          'resync hello carries member map': (m) => m.members !== null && typeof m.members === 'object' && !Array.isArray(m.members),
        });
        socket.send(JSON.stringify({ type: 'ping' }));
        socket.setTimeout(() => socket.close(), holdMs);
      }
      if (message.type === 'pong') {
        metrics.pongs.add(1);
        metrics.reconnects.add(1);
      }
    });
    socket.on('error', () => metrics.cyclesFailed.add(1));
  });

  check(res, { 'WS handshake is 101': (r) => r && r.status === 101 });
}
