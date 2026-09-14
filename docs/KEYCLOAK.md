# KEYCLOAK.md — corporate Keycloak (prod)

Real prod: **realm `h2-group`**, client **`ReportCollector`**, Keycloak at `https://portal.ruschem.ru`. Everything below is by the actual code (`front/src/app/api/auth/*`, `front/src/shared/auth/*`, `back/src/auth/*`) and the prod artifacts in `docs/keycloak/`. Replaces the previous corporate doc (outdated: it described the removed `AUTH_DEBUG_BOOTSTRAP` mode).

## Modes

- `KEYCLOAK_ENABLED=true` — OIDC login through Keycloak (prod).
- `KEYCLOAK_ENABLED=false` — local email+password login (Argon2): only for tests and e2e. Prod is always `true`.

## Login flow (Authorization Code Flow + PKCE S256)

1. `GET /api/auth/login` builds the authorize URL on `AUTH_KEYCLOAK_PUBLIC_ISSUER`: `client_id`, `code_challenge` (S256), `nonce`, `state`, `redirect_uri = ${AUTH_APP_ORIGIN}/api/auth/callback`, scope `openid profile email`. Transient cookies `rc_auth_state/rc_auth_verifier/rc_auth_nonce` (10 min).
2. `GET /api/auth/callback` — checks `state`, exchanges the `code` for tokens at `AUTH_KEYCLOAK_INTERNAL_ISSUER/protocol/openid-connect/token` (client auth `client_secret_basic` by default, `client_secret_post` — an option); verifies the ID token via JWKS (`.../protocol/openid-connect/certs`, algorithms from `AUTH_KEYCLOAK_SIGNING_ALGORITHMS`, audience = client id, issuer = `AUTH_KEYCLOAK_TOKEN_ISSUER`), checks `nonce` and `sub` against UserInfo.
3. Session — the **`rc_session`** cookie (payload + HMAC-SHA256 over `AUTH_SECRET`). Lifetime is its own, `AUTH_SESSION_MAX_AGE_SECONDS` (default **12 h**), and is **independent of the short Keycloak ID-token TTL** (minutes). Shorten it only deliberately: otherwise the user gets "not authorized" mid-work.
4. Identity for back: signed headers `X-ReportCollector-*` (`front/src/shared/auth/identity-headers.ts` → `back/src/auth/service.py::verify_signed_identity`), for WebSocket — a signed ticket in the query (`verify_identity_ticket`).

## Prod configuration

```dotenv
KEYCLOAK_ENABLED=true
AUTH_APP_ORIGIN=https://m2v-form-1.h2-group.ru   # NO trailing slash (else WS-403)
AUTH_SECRET=<shared with front, >= 32 chars>

AUTH_KEYCLOAK_PUBLIC_ISSUER=https://portal.ruschem.ru/auth/realms/h2-group
AUTH_KEYCLOAK_INTERNAL_ISSUER=https://portal.ruschem.ru/auth/realms/h2-group
AUTH_KEYCLOAK_DOCKER_INTERNAL_ISSUER=https://portal.ruschem.ru/auth/realms/h2-group
AUTH_KEYCLOAK_TOKEN_ISSUER=https://portal.ruschem.ru/auth/realms/h2-group
AUTH_KEYCLOAK_CLIENT_ID=ReportCollector
AUTH_KEYCLOAK_CLIENT_SECRET=<confidential client secret>   # NEVER in git
```

Optional: `AUTH_KEYCLOAK_SCOPES` (default `openid profile email`), `AUTH_KEYCLOAK_SIGNING_ALGORITHMS` (default `RS256`), `AUTH_KEYCLOAK_TOKEN_ENDPOINT_AUTH_METHOD` (default `client_secret_basic`), `AUTH_SESSION_MAX_AGE_SECONDS` (default `43200`).

The three issuer variables: **PUBLIC** — the browser (redirect), **INTERNAL** — token endpoint/JWKS when the front runs on the host, **DOCKER_INTERNAL** — from the front container. compose uses `AUTH_KEYCLOAK_INTERNAL_ISSUER=${AUTH_KEYCLOAK_DOCKER_INTERNAL_ISSUER:-${AUTH_KEYCLOAK_PUBLIC_ISSUER}}` (`compose.yaml`). For an external Keycloak all three coincide.

**Issuer nuance:** jose requires an exact match of the ID token's `iss`. Currently on prod `iss` arrives **with `https://`** (confirmed: `docs/keycloak/access-token-example.json`). Older Keycloak versions emitted `iss` without the scheme — if login suddenly fails verification, check the actual `iss` and fix only `AUTH_KEYCLOAK_TOKEN_ISSUER`.

## What Keycloak needs (client)

- confidential client `ReportCollector`, Authorization Code Flow, PKCE S256;
- exact redirect URI: `https://<dns-name>/api/auth/callback`;
- scopes `openid profile email`; web origins — the app host;
- the secret — only in `.env`, never in git or logs.

## Claims → user (mapping)

| Claim | Purpose | Stored in |
|---|---|---|
| `sub` | stable OIDC subject | `app_users.oidc_sub` |
| `email` | **main identifier**, case-insensitive (lowercased) | `app_users.email` |
| `preferred_username` | preferred username (identity headers, WS) | `app_users.preferred_username` |
| `name` | FIO, **overwrites** the local one on every login | `app_users.name` |
| `given_name` / `family_name` | parts of the FIO | identity headers, logs |
| `email_verified` | email-verification flag | `app_users.email_verified` |

`email` and `preferred_username` may come from different domains (`evtushenko.sn@ruschem.ru` vs `evtushenko.sn@h2-group.ru`) — that's normal. The system's user identifier is `email` (no separate key column: it would duplicate the normalized email).

## User provisioning (how it works now)

1. A project admin adds a person by email (lowercase) — they appear in **all** projects: `is_active=true` in the admin's project, `is_active=false` elsewhere (role `asset_worker`).
2. First successful Keycloak login (`upsert_current_user`): lookup by `oidc_sub` → email (lower); create if missing, fill in if present. FIO is overwritten from Keycloak, `sync_project_memberships` adds links to all projects (`is_active=false`), access appears where `is_active=true`.
3. Seed admin (`AUTH_SEED_ADMIN_EMAIL` + `AUTH_SEED_ADMIN_PASSWORD`): on first login becomes active with `admin` in all projects. (The old bootstrap mode `/auth/bootstrap` and `AUTH_DEBUG_BOOTSTRAP_*` are **removed** — don't look for them or document them.)
4. Blocked users (`is_deleted`) get `BlockedUserError` — access closed.

**Landmine:** whether the user can actually enter a project is decided by the **cross-project `user_roles` link** (`is_active=true`), not by the project's own dictionary row. `«/api/me» access` and the root-URL redirect read only `user_roles`. Accepting a person in the «Ремонт» «Пользователи» dictionary (`r_user_roles`) toggles `r_user_roles.is_active`, and every grant/revoke (create, `update_renovation_user`, and cell-undo) must mirror that onto the matching `user_roles` via `set_project_role_active`. Without the mirror a «Сырьё» member granted «Ремонт» access keeps being redirected to «Сырьё», and a revoked user still sees «Ремонт».
**Renovation roles and assets:** only the **`asset_worker`** role carries a per-asset grant list in the «Пользователи» dictionary. `admin`, `approver` and `block_owner` (Техническая дирекция/Дирекция КСиР/Дирекция МТО) **implicitly see all assets** — they never need an asset grant and their stored `r_user_assets` rows are cleared (`resolve_renovation_access` returns every asset for them). The dictionary shows these three roles with an empty «Активы» cell. UI label of the `approver` (Рогов) role is «Утверждающий» (not «Согласующий») everywhere.

## Local development

- Without Keycloak: `KEYCLOAK_ENABLED=false` → email+password form; e2e run on it (`front/e2e/auth.spec.ts`).
- Local Keycloak: `just keycloak` (standalone profile, `keycloak/keycloak:26.6`, realm imported from `infra/keycloak/import/reportcollector-realm.json`: realm `reportcollector`, client `ReportCollector`, secret `reportcollector-local-secret`; console `http://127.0.0.1:8080`, admin/admin). Env from `.env.example`:

```dotenv
AUTH_KEYCLOAK_PUBLIC_ISSUER=http://127.0.0.1:8080/realms/reportcollector
AUTH_KEYCLOAK_INTERNAL_ISSUER=http://127.0.0.1:8080/realms/reportcollector
AUTH_KEYCLOAK_DOCKER_INTERNAL_ISSUER=http://keycloak:8080/realms/reportcollector
```

## Diagnostics

- Logs (Loki, `{app="reportcollector"}`): `keycloak_identity_received` (identity data on login), `oidc_authentication_succeeded` / `oidc_authentication_failed` (front, callback), `user_created`, `blocked_user_attempt`.
- **WS handshake 403** → Origin not in the CORS list: almost always a trailing slash in `AUTH_APP_ORIGIN`. Remove the slash and restart.
- **"Not authorized" mid-work** → the `rc_session` cookie expired (`AUTH_SESSION_MAX_AGE_SECONDS`); the ID token is irrelevant here.
- **ID token verification error** → compare the actual `iss` with `AUTH_KEYCLOAK_TOKEN_ISSUER`, the signing algorithm with `AUTH_KEYCLOAK_SIGNING_ALGORITHMS`.
- Corporate CA (if Keycloak isn't public): `NODE_EXTRA_CA_CERTS` points to a PEM. For `portal.ruschem.ru` a public CA — not needed.

## Security

- The discovery document is **public** (served without auth at `.../.well-known/openid-configuration`): `docs/keycloak/oidc-discovery-prod.json` — no secrets in it.
- A real access token contains **personal data** (FIO, email) and session identifiers (`sub`/`sid`/`jti`) — the repo holds only a **sanitized** copy: `docs/keycloak/access-token-example.json`.
- Secrets (client secret, `AUTH_SECRET`, passwords) — only `.env`; never in git or logs.
