// Shared setup for the k6 load scripts. No runtime dependency beyond k6 itself:
// native k6/ws, k6/http and k6/metrics only.
import http from 'k6/http';
import { Counter, Trend } from 'k6/metrics';

const num = (name, fallback) => {
  const raw = __ENV[name];
  if (raw === undefined || raw === '') return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value)) throw new Error(`${name} must be a number`);
  return value;
};

export const cfg = {
  frontUrl: (__ENV.RC_FRONT_URL || 'http://127.0.0.1:16000').replace(/\/$/, ''),
  wsUrl: (__ENV.RC_WS_URL || 'ws://127.0.0.1:19000/api/ws/raw-materials').replace(/\/$/, ''),
  email: __ENV.RC_TEST_EMAIL || 'user1@test.local',
  password: __ENV.E2E_TEST_PASSWORD || '',
  project: __ENV.RC_PROJECT || 'raw_materials',
  section: __ENV.RC_ROOM_SECTION || 'materialMovement',
  month: __ENV.RC_REPORT_MONTH || '2026-08-01',
  cells: num('RC_CELLS', 1),
  rows: num('RC_ROWS', 8),
  pingEveryMs: num('RC_PING_EVERY_MS', 5000),
};

export const metrics = {
  helloRtt: new Trend('ws_hello_rtt_ms', true),
  pongRtt: new Trend('ws_pong_rtt_ms', true),
  leaseRtt: new Trend('ws_lease_granted_rtt_ms', true),
  roomMessages: new Counter('ws_room_messages_received'),
  presenceUpdates: new Counter('presence_updates_received'),
  pongs: new Counter('pongs_received'),
  handshakes: new Counter('ws_handshakes_ok'),
  cyclesOk: new Counter('edit_cycles_ok'),
  cyclesFailed: new Counter('edit_cycles_failed'),
  reconnects: new Counter('ws_reconnects'),
  restPatch: new Trend('rest_patch_ms', true),
  restUndo: new Trend('rest_undo_ms', true),
  restRedo: new Trend('rest_redo_ms', true),
};

export function requirePassword() {
  if (!cfg.password) {
    throw new Error('E2E_TEST_PASSWORD is required (isolated stand .test identity only).');
  }
}

/** Local login through the front proxy; the cookie is the only session state. */
export function login() {
  requirePassword();
  const body = `email=${encodeURIComponent(cfg.email)}&password=${encodeURIComponent(cfg.password)}`;
  const res = http.post(`${cfg.frontUrl}/api/auth/login`, body, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    redirects: 0,
    tags: { name: 'auth.login' },
  });
  if (res.status !== 303) {
    throw new Error(`local login failed: HTTP ${res.status}`);
  }
  const cookie = res.cookies && res.cookies.rc_session && res.cookies.rc_session[0];
  if (!cookie || !cookie.value) {
    throw new Error('local login did not set rc_session');
  }
  return { headers: { Cookie: `rc_session=${cookie.value}` } };
}

/** One-shot signed ticket, minted per connection attempt (60 s TTL). */
export function ticket(session) {
  const res = http.get(`${cfg.frontUrl}/api/collab/ticket?project=${encodeURIComponent(cfg.project)}`, {
    headers: session.headers,
    tags: { name: 'collab.ticket' },
  });
  if (res.status !== 200) {
    throw new Error(`ticket mint failed: HTTP ${res.status}`);
  }
  const value = res.json('ticket');
  if (!value) throw new Error('ticket response has no ticket');
  return value;
}

/** Real row ids from the seeded room; never a fabricated cell key. */
export function movementRowIds(session) {
  const res = http.get(
    `${cfg.frontUrl}/api/raw-materials/movement?report_month=${encodeURIComponent(cfg.month)}`,
    { headers: session.headers, tags: { name: 'movement.rows' } },
  );
  if (res.status !== 200) {
    throw new Error(`movement read failed: HTTP ${res.status}`);
  }
  const rows = res.json();
  if (!Array.isArray(rows) || rows.length === 0) {
    throw new Error('movement read returned no rows; seed the isolated stand first');
  }
  return rows.slice(0, cfg.rows).map((row) => row.id);
}

export function cellKeys(rowIds, count) {
  const keys = [];
  for (let i = 0; i < count; i += 1) {
    keys.push(`rm_material_movements/${rowIds[i % rowIds.length]}/demandPlanTons`);
  }
  return keys;
}

export function socketUrl(ticketValue, clientId, sessionId) {
  const query = [
    `project=${encodeURIComponent(cfg.project)}`,
    `section=${encodeURIComponent(cfg.section)}`,
    `reportMonth=${encodeURIComponent(cfg.month)}`,
    `clientId=${encodeURIComponent(clientId)}`,
    `sessionId=${encodeURIComponent(sessionId)}`,
    `ticket=${encodeURIComponent(ticketValue)}`,
  ].join('&');
  return `${cfg.wsUrl}?${query}`;
}

export function writeBody(rowIds, keys, value) {
  const updates = keys.map((key, index) => {
    const rowId = key.split('/')[1] || rowIds[index % rowIds.length];
    return { rowId, field: 'demandPlanTons', value };
  });
  return JSON.stringify({ reportMonth: cfg.month, updates });
}

export function restPatch(session, rowIds, keys, sessionId, value) {
  const res = http.patch(
    `${cfg.frontUrl}/api/raw-materials/movement?session_id=${encodeURIComponent(sessionId)}`,
    writeBody(rowIds, keys, value),
    {
      headers: { ...session.headers, 'Content-Type': 'application/json' },
      tags: { name: 'movement.patch' },
    },
  );
  metrics.restPatch.add(res.timings.duration);
  return res;
}

export function restUndoRedo(session, action, sessionId, body) {
  const res = http.post(
    `${cfg.frontUrl}/api/raw-materials/${action}?session_id=${encodeURIComponent(sessionId)}`,
    JSON.stringify(body),
    {
      headers: { ...session.headers, 'Content-Type': 'application/json' },
      tags: { name: `movement.${action}` },
    },
  );
  if (action === 'undo') metrics.restUndo.add(res.timings.duration);
  else metrics.restRedo.add(res.timings.duration);
  return res;
}
