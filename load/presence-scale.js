// (c) Presence at scale: N sockets in ONE room (worst-case full-map fan-out).
// Every heartbeat publishes the whole member map to every member.
import { check } from 'k6';
import ws from 'k6/ws';
import { cfg, login, metrics, socketUrl, ticket } from './lib/common.js';

const vus = Number(__ENV.RC_VUS || 50);
const duration = __ENV.RC_DURATION || '20s';
const socketMs = Number(__ENV.RC_SOCKET_MS || 20000);

export const options = {
  scenarios: {
    presence: { executor: 'constant-vus', exec: 'presence', vus, duration },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

let session;

export function presence() {
  if (!session) session = login();
  const value = ticket(session);
  const clientId = `load-pr-${__VU}-${__ITER}`;
  const sessionId = `${clientId}-${Date.now()}`;
  const pings = [];

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
      if (message.type === 'pong') {
        metrics.pongs.add(1);
        const sentAt = pings.shift();
        if (sentAt !== undefined) metrics.pongRtt.add(Date.now() - sentAt);
      }
    });
    socket.on('error', () => metrics.cyclesFailed.add(1));
    socket.setInterval(() => {
      pings.push(Date.now());
      socket.send(JSON.stringify({ type: 'ping' }));
    }, cfg.pingEveryMs);
    socket.setTimeout(() => socket.close(), socketMs);
  });

  check(res, { 'WS handshake is 101': (r) => r && r.status === 101 });
}
