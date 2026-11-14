# F5: Crew-network hub, access codes, roles

This is the phase-2 spec. It is built after the phase-1 features are merged. It is a
cross-cutting change, so a single engineer builds it, and that engineer may edit any router
or page needed to enforce authorization.

## Threat model and goals

**Setting:** several crew computers on a closed habitat LAN. There is no internet. One
computer runs the AM hub, and the others use a browser.

**Assets:** the crew's health records, conversations, alerts and journals.

**Threats addressed:**
- Another device on the LAN reading or changing records without signing in.
- A crew member reading another crew member's medical records.
- Brute-forcing access codes.
- Cross-site requests (CSRF) from another origin.
- Leaking tokens or codes through the database, logs or audit payloads.

**Not addressed (state this in the UI and docs):** traffic encryption on the LAN, because
there is no HTTPS. The hub assumes a closed network.

## Activation (two keys, off by default)

Hub mode is **active** only when both of these hold:
1. The setting `crew_network_enabled` is true. Only an operator can change it.
2. The server process runs with `AM_CREW_NETWORK=1` (`Settings.crew_network: bool`).

The run script `scripts/run_hub.ps1` (and `.sh`) does this:
- sets `AM_CREW_NETWORK=1`
- builds the frontend
- starts uvicorn on `0.0.0.0:8000`
- prints the LAN URLs

**Allowed hosts.** When hub mode is active, `TrustedHostMiddleware` must accept the hub's
LAN IPs and hostname. Detect them at startup and allow extra ones through
`AM_HUB_HOSTS=host1,host2`. Otherwise keep the current localhost-only list.

**Serving the frontend.** If `frontend/dist` exists, the backend serves it at `/` with an
SPA fallback to `index.html` for non-`/api` paths, so LAN browsers need only port 8000.

**When hub mode is inactive,** behaviour is exactly as today: single-user local operation,
where every request acts as `operator`. All existing tests must pass unchanged.

## Identity

**Principal:** `{role: crew|medical|operator, astronaut_id|None, device_name, source: local|loopback|session, session_id?}`.

`get_principal(request, db)` decides the principal:

| Condition | Principal |
|---|---|
| Hub inactive | `operator`, source `local` |
| Hub active, client host is loopback (127.0.0.1 or ::1) | `operator`, source `loopback` (the hub console) |
| Hub active, otherwise | Requires a valid, unexpired, non-revoked `DeviceSession` from cookie `am_session` (HttpOnly, SameSite=Strict, Path=/), or `Authorization: Bearer`. Missing → **401**. |

Each authenticated request updates `last_seen_at`.

**Access codes:**
- Generated server-side: 16 characters of Crockford base32, grouped `XXXX-XXXX-XXXX-XXXX`.
- Shown **once**, then stored only as a salted scrypt hash.
- Have a role and a label. Crew codes are bound to an astronaut.
- Revocable; revocation also revokes the code's sessions.

**Sign-in:** `POST /network/sessions` with `{code, device_name}`.
- Creates a `DeviceSession` with a random 32-byte token, stored as SHA-256.
- Expiry: 12 h by default, configurable.
- Sets the cookie.
- Rate limit: N failed attempts per client IP in a window returns 429. Use a named
  constant and document it.
- Audit every success and failure. Never log the code or the token.

**Sign-out:** `DELETE /network/sessions/current`.

## Authorization

- Every route declares a policy through a small registry or decorator:
  - `public`: health check, sign-in, `me` and status
  - `operator`: settings, backups, outbox, access codes, simulation, knowledge ingest,
    device management
  - `medical_or_operator`: crew-wide lists, the crew log, ground reports
  - `astronaut_scoped`
- **Astronaut-scoped routes:** `crew` principals may only access their own `astronaut_id`.
  - Resolve indirect ids through their owner:
    - an alert, finding, observation, journal entry, task, symptom report or proposal →
      its astronaut
    - a conversation → its astronaut
  - Return 403 when the astronaut differs, and 404 when the object does not exist.
- **List endpoints without an astronaut filter** are automatically limited to the crew
  member's own astronaut. Examples: `GET /alerts`, `/astronauts`, `/audit`.
- **Authorization coverage test:** iterate over `app.routes` and assert that every
  `/api/v1` route has a declared policy. Then exercise a representative set of routes for
  each policy with three principals: crew A, crew B and medical.

## Message provenance and crew log

- `CompanionService.send` receives the principal and stores `device_name`,
  `device_session_id` and `client_host` on the user message.
- `GET /network/crew-log?since=&until=&astronaut_id=` is `medical_or_operator`. It returns
  all user and assistant messages across the crew, with timestamp, astronaut, device and
  conversation.
- Crew members see their own history through the existing endpoints.

## API

| Endpoint | Policy | Notes |
|---|---|---|
| `GET /network/status` | public | Returns `{setting_enabled, env_enabled, active, hub_urls, https: false}` |
| `GET /network/me` | public | Principal, or 401 when hub mode requires sign-in |
| `POST /network/sessions` | public | Sign in |
| `DELETE /network/sessions/current` | public | Sign out |
| `GET`, `POST /network/access-codes`; `POST /network/access-codes/{id}/revoke` | operator | |
| `GET /network/devices`; `POST /network/devices/{id}/revoke` | operator | |
| `GET /network/crew-log` | medical_or_operator | |

## Frontend

- **Session state.** `session.tsx` loads `/network/me`.
  - On 401, show a sign-in page with the access code and a device name (remembered in
    `localStorage`).
  - For `crew` principals, hide the astronaut picker and fix the astronaut.
  - Show the signed-in identity and a "Sign out" button in the Shell header.
- **Settings.** Add a "Crew network" section:
  - the two-key status, with exact instructions for the `run_hub` script
  - an enable toggle (operator)
  - create an access code, showing the code once with a copy affordance
  - revoke codes
  - active devices
  - a clear warning that LAN traffic is unencrypted
- **Crew log page,** for `medical` and `operator` roles: a filterable conversation timeline
  across the crew, with timestamps and device names.
