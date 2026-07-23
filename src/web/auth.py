"""
KP4PRA TNC - Dashboard authentication (session cookie login).

No new dependencies: scrypt password hashing and HMAC-signed session
tokens use only the Python standard library.

Gating rule (WEBMAIL spec section 3):
  * If no dashboard password hash is configured, the Admin Dashboard is
    OPEN (first-run / not yet secured) so the trustee can perform the
    initial station configuration and then set a password.
  * Once a dashboard password hash IS configured, every dashboard page
    and administrative API endpoint requires a valid session cookie.
The public Web Email Interface is never gated by this module.

This module is deliberately framework-agnostic (no FastAPI imports) so
the crypto can be unit-tested on its own; web_app.py wires the FastAPI
dependencies around it.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from common.config import load_config

SESSION_COOKIE = "kp4pra_session"
CSRF_COOKIE = "kp4pra_csrf"
SESSION_TTL = 12 * 3600  # seconds (12 hours)

_SCRYPT_N = 1 << 14
_SCRYPT_R = 8
_SCRYPT_P = 1

MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 256

# Placeholder callsigns that do NOT count as "configured".
INVALID_CALLSIGNS = {"", "N0CALL"}

_secret_cache = None
_ephemeral_key = None


# ─────────────────────────────────────────────────────────────────────────────
# Persistent signing key
# ─────────────────────────────────────────────────────────────────────────────

def _data_dir() -> str:
    return load_config()["paths"]["data"]


def _make_ephemeral() -> bytes:
    global _ephemeral_key
    if _ephemeral_key is None:
        _ephemeral_key = secrets.token_bytes(32)
    return _ephemeral_key


def _secret_key() -> bytes:
    """Load or lazily create the persistent 32-byte HMAC signing key.

    Stored under paths.data (/rw) mode 0600 so sessions survive a service
    restart or reboot. If the directory cannot be written (e.g. a
    read-only first boot), fall back to a process-lifetime random key so
    login still works within the current run.
    """
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache

    path = os.path.join(_data_dir(), "session.key")
    try:
        with open(path, "rb") as f:
            key = f.read()
        if len(key) >= 32:
            _secret_cache = key
            return key
    except FileNotFoundError:
        pass
    except Exception:
        return _make_ephemeral()

    key = secrets.token_bytes(32)
    try:
        os.makedirs(_data_dir(), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        _secret_cache = key
        return key
    except Exception:
        return _make_ephemeral()


# ─────────────────────────────────────────────────────────────────────────────
# Password hashing (scrypt)
# ─────────────────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Return an encoded scrypt hash: scrypt$N$r$p$salt_hex$hash_hex."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32,
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, salt.hex(), dk.hex())


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verify a password against an encoded scrypt hash."""
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(hash_hex)
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def validate_new_password(pw: str) -> tuple:
    """Return (ok, message) for a proposed new dashboard password."""
    if not isinstance(pw, str):
        return False, "Password must be text."
    if len(pw) < MIN_PASSWORD_LEN:
        return False, "Password must be at least %d characters." % MIN_PASSWORD_LEN
    if len(pw) > MAX_PASSWORD_LEN:
        return False, "Password is too long."
    if not pw.strip():
        return False, "Password cannot be blank."
    return True, ""


# ─────────────────────────────────────────────────────────────────────────────
# Signed session tokens
# ─────────────────────────────────────────────────────────────────────────────

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(txt: str) -> bytes:
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


def _sign(payload: bytes) -> str:
    body = _b64e(payload)
    sig = hmac.new(_secret_key(), body.encode("ascii"), hashlib.sha256).digest()
    return body + "." + _b64e(sig)


def _unsign(token: str):
    try:
        body_s, sig_s = token.split(".", 1)
        expected = hmac.new(_secret_key(), body_s.encode("ascii"),
                            hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64d(sig_s)):
            return None
        return json.loads(_b64d(body_s))
    except Exception:
        return None


def issue_session() -> str:
    payload = json.dumps({"exp": int(time.time()) + SESSION_TTL}).encode("ascii")
    return _sign(payload)


def session_valid(token: str) -> bool:
    data = _unsign(token or "")
    if not data:
        return False
    try:
        return int(data.get("exp", 0)) > int(time.time())
    except (TypeError, ValueError):
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CSRF (double-submit cookie)
# ─────────────────────────────────────────────────────────────────────────────

def issue_csrf() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(cookie_val, header_val) -> bool:
    if not cookie_val or not header_val:
        return False
    return hmac.compare_digest(str(cookie_val), str(header_val))


# ─────────────────────────────────────────────────────────────────────────────
# Gating helpers
# ─────────────────────────────────────────────────────────────────────────────

def dashboard_password_set(cfg=None) -> bool:
    cfg = cfg or load_config()
    return bool(str(cfg.get("web", {}).get("dashboard_password_hash", "")).strip())


def callsign_valid(cfg=None) -> bool:
    cfg = cfg or load_config()
    cs = str(cfg.get("station", {}).get("callsign", "")).strip().upper()
    return cs not in INVALID_CALLSIGNS


def auth_required(cfg=None) -> bool:
    """Dashboard auth is enforced exactly when a password has been set."""
    return dashboard_password_set(cfg)


def safe_next(path: str) -> str:
    """Constrain a post-login redirect target to a local path (no open
    redirect). Anything suspicious falls back to the dashboard home."""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        return "/admin"
    return path
