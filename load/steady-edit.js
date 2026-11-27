// (b) Steady editing: lease acquire -> REST PATCH -> lease release, per cell.
import { check } from 'k6';
import { sleep } from 'k6';
import ws from 'k6/ws';
import {
  cellKeys,
  cfg,
  login,
  metrics,
  movementRowIds,
  restPatch,
  socketUrl,
  ticket,
} from './lib/common.js';

const vus = Number(__ENV.RC_VUS || 20);
const duration = __ENV.RC_DURATION || '30s';
const timeoutMs = Number(__ENV.RC_SOCKET_TIMEOUT_MS || 20000);

export const options = {
  scenarios: {
    steadyEdit: { executor: 'constant-vus', exec: 'steadyEdit', vus, duration },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

let session;
let rowIds;

export function steadyEdit() {
  if (!session) {
    session = login();
    rowIds = movementRowIds(session);
  }
  const value = ticket(session);
  const clientId = `load-se-${__VU}-${__ITER}`;
  const sessionId = `${clientId}-${Date.now()}`;
  const offset = ((__VU - 1) * cfg.cells) % rowIds.length;
  const rotated = rowIds.slice(offset).concat(rowIds);
  const keys = cellKeys(rotated, cfg.cells);
  const cellValue = 1 + ((__VU + __ITER) % 97);
  let phase = 'connect';
  let grantStart = 0;
  let finished = false;

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
      if (message.type === 'hello' && phase === 'connect') {
        phase = 'acquire';
        grantStart = Date.now();
        socket.send(JSON.stringify({ type: 'lease:acquire', cells: keys }));
        return;
      }
      if (message.type === 'lease:granted' && phase === 'acquire') {
        metrics.leaseRtt.add(Date.now() - grantStart);
        phase = 'patch';
        const patched = restPatch(session, rotated, keys, sessionId, cellValue);
        check(patched, { 'PATCH /movement is 200': (r) => r.status === 200 });
        if (patched.status !== 200) {
          metrics.cyclesFailed.add(1);
          finished = true;
          socket.close();
          return;
        }
        phase = 'release';
        socket.send(JSON.stringify({ type: 'lease:release', cells: keys }));
        return;
      }
      if (message.type === 'lease:denied' && phase === 'acquire') {
        metrics.cyclesFailed.add(1);
        phase = 'release';
        socket.send(JSON.stringify({ type: 'lease:release', cells: keys }));
        return;
      }
      if (message.type === 'cell:unlocked' && phase === 'release' && keys.includes(message.key)) {
        metrics.cyclesOk.add(1);
        finished = true;
        socket.close();
      }
    });
    socket.on('error', () => {
      if (!finished) metrics.cyclesFailed.add(1);
      finished = true;
    });
    socket.setTimeout(() => {
      if (!finished) {
        metrics.cyclesFailed.add(1);
        socket.close();
      }
    }, timeoutMs);
  });

  check(res, { 'WS handshake is 101': (r) => r && r.status === 101 });
  sleep(0.05);
}
