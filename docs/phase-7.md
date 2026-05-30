# Phase 7 — Multi-user hosting & authentication

**Status:** planned (not started). Documented per operator request after a
design discussion; do not start before it is explicitly prioritized.

**Goal:** make the tool safely usable by **multiple concurrent users on a single
Linux host**. Each user logs in, runs their own compares, and sees only their
own saved configs and run history. A single **admin-only page** manages users:
create a login, issue an initial password, and reset/regenerate passwords. The
**first login forces a password change**. No RBAC beyond *user* vs *admin*.

## Context (what exists today)

- The FastAPI backend has **no authentication** — every `/api/*` endpoint is
  open to anyone who can reach the port.
- Saved configs are **global**: `user_configs/<name>/` is a flat shared
  namespace. The SQLite `runs` / `configs` tables (ADR-043) are likewise global.
  Any caller can see, run, overwrite, or list everyone else's work.
- Input/output locations (`file_a`, `file_b`, `output_dir`) and `GET /api/browse`
  are **operator-typed absolute server paths** — there is no upload flow and no
  per-user filesystem boundary.
- There are two front-ends. **`ui2/` (Next.js) is the target** for this phase;
  the Vue `ui/` becomes legacy and is **not** updated for auth (ADR-044 already
  frames `ui/` as the reference implementation and `ui2/` as the going-forward
  surface).

## Agreed decisions (from the design discussion)

1. **Auth** = cookie-based **server-side sessions** + **bcrypt** password
   hashing (not JWT). See ADR-045.
2. **Login lands only in `ui2/`.** The Vue `ui/` stops working against the
   authed API and is treated as legacy.
3. **Per-user isolation is in scope** — login alone is not enough; configs and
   run history are scoped to the owning user.
4. **Typed server paths are kept** (trusted-user model). No upload/sandbox work
   this phase; `/api/browse` stays but becomes auth-gated. The residual trust
   assumption (all logged-in users are trusted on that box) is documented, not
   engineered away.

## Scope

### 1. Authentication (cookie sessions + bcrypt)

- **`users` table** in `api/db.py`: `id, username (unique), password_hash
  (bcrypt), is_admin, must_change_password, disabled, created_at, updated_at`.
- **`sessions` table** (shared across gunicorn workers — in-memory would not be):
  `session_id (opaque random), user_id, created_at, expires_at, last_seen`.
- **Session cookie**: `httpOnly` + `Secure` + `SameSite=Lax`; the value is an
  opaque id looked up server-side. TTL from `SEGCMP_SESSION_TTL`.
- **Endpoints**: `POST /api/auth/login`, `POST /api/auth/logout`,
  `GET /api/auth/me`, `POST /api/auth/change-password`.
- **Guard**: a FastAPI dependency protects all `/api/*` except `auth/login` and
  `health`. Admin routes add an admin-only dependency.
- **Bootstrap admin**: on startup, if no admin exists, seed one from
  `SEGCMP_ADMIN_USER` / `SEGCMP_ADMIN_PASSWORD` with `must_change_password=true`,
  so the real password is set at first login.

### 2. Admin user management (one page)

- **Endpoints (admin only)**: `GET /api/admin/users`,
  `POST /api/admin/users` (create; the server **generates** a strong initial
  password, returned **once**), `POST /api/admin/users/{id}/reset-password`
  (regenerate, returned once, sets `must_change_password=true`),
  and an enable/disable toggle.
- **`ui2` single page `/admin`**: a users table plus "Add user" and
  "Reset password" actions; the generated password shows once in a copyable
  field. The sidebar entry and route are **gated on `is_admin`**; non-admins get
  403 from the API and never see the link. Deliberately minimal — one page, no
  heavy admin console.

### 3. First-login password change

- `must_change_password` forces the change-password screen. Until it is cleared,
  the API rejects every endpoint except `auth/me`, `auth/change-password`, and
  `auth/logout` (so a user can't run or read anything with a temp password).

### 4. Per-user isolation

- **Configs namespaced by user**: `user_configs/<username>/<config>/` keeps the
  filesystem source of truth aligned with ownership. `storage` functions take
  the current user; `save/list/get/run` are all scoped.
- **SQLite ownership**: add `user_id` to `runs` and `configs`; every read
  (`dashboard`, `history`, `list_configs`, `get_config`) **filters by the
  logged-in user**, every write stamps the owner. `backfill_from_disk` assigns
  pre-existing rows to a legacy/default owner.

## Out of scope

- **File upload / filesystem sandboxing** — typed server paths retained
  (trusted-user model); `/api/browse` stays, auth-gated. A future phase can add
  per-user upload + a managed results root if untrusted users are introduced.
- Roles/permissions beyond user vs admin; SSO / LDAP / OAuth; self-service email
  password reset (resets are admin-issued).
- Auth for the Vue `ui/`.
- Rate limiting / account lockout (note as a possible follow-on).
- Changing the comparison engine — auth is an `api/` + `ui2/` concern only.

## Deployment notes

- Run **uvicorn under gunicorn with N workers behind nginx** (TLS termination).
  `Secure` cookies require HTTPS.
- **Sessions live in SQLite** so they are shared across workers.
- SQLite **WAL** is fine at this concurrency; Postgres is the swap-in if write
  contention ever shows up.
- Runs are still **synchronous and block a worker** for their duration — size
  the worker count for expected concurrent runs; a job queue remains a Phase 4/5
  follow-on, not part of Phase 7.
- **New env vars**: `SEGCMP_ADMIN_USER`, `SEGCMP_ADMIN_PASSWORD`,
  `SEGCMP_SESSION_TTL`.
- **New dependency**: `passlib[bcrypt]` (the engine stays stdlib-only; this is an
  `api/` dependency).

## Exit criteria (draft)

1. Unauthenticated requests to any non-public endpoint return **401**; login
   sets a session cookie; logout invalidates the session.
2. An admin can **create a user** who receives a one-time initial password and is
   **forced to change it at first login**; the admin can **reset/regenerate** it.
3. The admin page is reachable and visible **only to admins**; non-admins get
   403 on admin endpoints and see no sidebar entry.
4. A user sees **only their own** configs and run history/dashboard; another
   user's data is invisible and cannot be modified.
5. Passwords are stored only as **bcrypt hashes**; sessions in SQLite; cookies
   `httpOnly`+`Secure`+`SameSite`.
6. `passlib[bcrypt]` added; tests cover login, the guard, admin actions, and
   per-user isolation; `black` / `flake8` / `mypy` clean; **ADR-045** records the
   approach and trade-offs.
