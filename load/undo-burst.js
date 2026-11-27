// (d) Undo bursts: acquire -> PATCH -> undo -> redo -> release per cell.
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
  restUndoRedo,
  socketUrl,
  ticket,
} from './lib/common.js';

const vus = Number(__ENV.RC_VUS || 10);
const duration = __ENV.RC_DURATION || '30s';
const timeoutMs = Number(__ENV.RC_SOCKET_TIMEOUT_MS || 20000);

export const options = {
  scenarios: {
    undoBurst: { executor: 'constant-vus', exec: 'undoBurst', vus, duration },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

let session;
let rowIds;

export function undoBurst() {
  if (!session) {
    session = login();
    rowIds = movementRowIds(session);
  }
  const value = ticket(session);
  const clientId = `load-ub-${__VU}-${__ITER}`;
  const sessionId = `${clientId}-${Date.now()}`;
  const offset = ((__VU - 1) * cfg.cells) % rowIds.length;
  const rotated = rowIds.slice(offset).concat(rowIds);
  const keys = cellKeys(rotated, cfg.cells);
  const cellValue = 10 + ((__VU + __ITER) % 89);
  let phase = 'connect';
  let finished = false;
  const body = { section: cfg.section, reportMonth: cfg.month };

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
        socket.send(JSON.stringify({ type: 'lease:acquire', cells: keys }));
        return;
      }
      if (message.type === 'lease:granted' && phase === 'acquire') {
        phase = 'patch';
        const patched = restPatch(session, rotated, keys, sessionId, cellValue);
        if (patched.status !== 200) {
          metrics.cyclesFailed.add(1);
          finished = true;
          socket.close();
          return;
        }
        const undone = restUndoRedo(session, 'undo', sessionId, body);
        check(undone, { 'undo is 200': (r) => r.status === 200 });
        const redone = restUndoRedo(session, 'redo', sessionId, body);
        check(redone, { 'redo is 200': (r) => r.status === 200 });
        if (undone.status !== 200 || redone.status !== 200) metrics.cyclesFailed.add(1);
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
