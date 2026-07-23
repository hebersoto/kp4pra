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

## Phase 2 — Public Web Email Interface & holding queue

### Routes (public, no Dashboard auth)
- `GET /mail?lang=en|es` — language toggle, mandatory notice + agreement
  (spec section 7), then the compose form. English is the default.
- `POST /mail/submit` — JSON body {to, reply_to, subject, body, lang,
  csrf}. Validates, enqueues, returns {success, redirect} or
  {success:false, errors|message}. Never transmits.
- `GET /mail/sent` — confirmation (spec section 15): held for review, not
  transmitted.

### Message rules (spec section 5)
Plain text only; no HTML; no attachments (the form has no file input).
Destination and Reply-To required and validated with email-validator when
installed, else a conservative ASCII fallback. Internationalized
(non-ASCII) addresses are rejected with a clear message; user content is
never silently truncated. Generated lines wrap to <=78 chars in Phase 4.

### Holding queue (spec section 9)
mailqueue.py stores one atomic JSON file per message under
<paths.data>/mailq/ (/rw/kp4pra-tnc/data/mailq) - persistent, not tmpfs.
States: Holding, Approved, Sending, Sent, Failed, Rejected. Message IDs
are server-generated and pattern-validated on every lookup (no path
traversal); there is no public read endpoint, so users cannot read other
users' messages.

### Security (spec section 17)
Public input validated/sanitized; nothing reaches a shell. Public
double-submit CSRF token (kp4pra_mail_csrf) guards /mail/submit. Jinja
autoescaping renders stored values inertly.

### Configuration
webmail.enabled (default true) hides/shows the composer; missing key is
backward compatible.
