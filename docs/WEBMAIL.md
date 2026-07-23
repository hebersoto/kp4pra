# KP4PRA TNC — Web Email Interface & Admin Dashboard

Phase 1 adds a public landing page and session-based Admin Dashboard
authentication. Route map: `/` public landing; `/admin` the former
dashboard (session-gated once a password is set); `/admin/login`,
`/admin/logout`; `/api/dashboard/password` to set/change the password.
All prior admin pages/APIs keep their URLs and are now gated by the same
session. `/api/version` stays public.

First-run grace: auth is enforced only after a Dashboard password is set.
Fresh boards (no valid callsign) stay open so the trustee can configure
the station, then set a password on the Config page.

Auth internals (stdlib only): scrypt password hash in
`web.dashboard_password_hash`; HMAC-signed expiring session cookie
`kp4pra_session` (signing key at `<paths.data>/session.key`, 0600);
CSRF double-submit via the readable `kp4pra_csrf` cookie echoed as
`X-CSRF-Token`. Legacy `web.auth_enabled/username/password` are retained
for back-compat but unused.

Recovery: blank `web.dashboard_password_hash` in
`/rw/kp4pra-tnc/config.yaml` and restart `kp4pra-tnc-web` to reopen the
dashboard.
