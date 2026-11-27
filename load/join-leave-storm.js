// (a) Join/leave storm: VUs open a real collaboration socket, wait for hello,
// then close. Run against the disposable isolated stand only.
import { check } from 'k6';
import { sleep } from 'k6';
import ws from 'k6/ws';
import { cfg, login, metrics, socketUrl, ticket } from './lib/common.js';

const vus = Number(__ENV.RC_VUS || 50);
const duration = __ENV.RC_DURATION || '30s';
const holdMs = Number(__ENV.RC_HOLD_MS || 300);

export const options = {
  scenarios: {
    joinLeave: { executor: 'constant-vus', exec: 'joinLeave', vus, duration },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

let session;

export function joinLeave() {
  if (!session) session = login();
  const value = ticket(session);
  const clientId = `load-jl-${__VU}-${__ITER}`;
  const sessionId = `${clientId}-${Date.now()}`;
  const start = Date.now();

  const res = ws.connect(socketUrl(value, clientId, sessionId), {}, (socket) => {
    socket.on('open', () => {
      metrics.handshakes.add(1);
    });
    socket.on('message', (raw) => {
      metrics.roomMessages.add(1);
      let message;
      try {
        message = JSON.parse(raw);
      } catch {
        return;
      }
      if (message.type === 'hello') {
        metrics.helloRtt.add(Date.now() - start);
        check(message, { 'hello names the room': (m) => typeof m.room === 'string' && m.room.length > 0 });
        socket.setTimeout(() => socket.close(), holdMs);
      }
    });
    socket.on('error', () => {
      metrics.cyclesFailed.add(1);
    });
  });

  check(res, { 'WS handshake is 101': (r) => r && r.status === 101 });
  sleep(0.05);
}
