"""
KP4PRA TNC - Web Email validation and plain-text message formatting.

Validation goals (WEBMAIL spec section 5):
  * Valid destination and Reply-To addresses are required.
  * Use a reliable validator (email-validator) rather than hand-rolling
    the full RFC grammar; fall back to a conservative stdlib check only
    if the library is unavailable so the app never hard-fails.
  * Support RFC 5321/5322 addresses. Internationalized (RFC 6531 /
    SMTPUTF8) addresses are REJECTED with a clear message, because the
    downstream Winlink/CMS transport is ASCII-only — we reject rather
    than silently modify (spec section 5).
  * Plain text only; no HTML; no attachments (enforced structurally by
    the form, which has no file input).

Returned error values are stable KEYS (e.g. "email_invalid"); the web
layer maps them to English/Spanish user-facing text.
"""

import re
import textwrap

# Soft guidance limits (counters); not hard blocks.
SUBJECT_SOFT_LIMIT = 50
BODY_SOFT_LIMIT = 300
# Hard sanity caps to bound queue storage and prevent abuse.
SUBJECT_HARD_MAX = 200
BODY_HARD_MAX = 8000
EMAIL_HARD_MAX = 254          # RFC 5321 forward-path maximum
WRAP_WIDTH = 78               # spec: generated lines must not exceed 78

try:
    from email_validator import validate_email as _ev_validate, \
        EmailNotValidError as _EVError
    _HAVE_EV = True
except Exception:  # pragma: no cover - depends on board venv
    _HAVE_EV = False

# Conservative RFC 5322-ish ASCII fallback (dot-atom local part + domain).
_FALLBACK_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def _is_ascii(s: str) -> bool:
    try:
        s.encode("ascii")
        return True
    except UnicodeError:
        return False


def check_address(addr: str):
    """Validate one email address.

    Returns (ok, normalized, error_key). error_key is None on success,
    otherwise one of: "email_required", "email_invalid",
    "email_intl_unsupported".
    """
    addr = (addr or "").strip()
    if not addr:
        return False, "", "email_required"
    if len(addr) > EMAIL_HARD_MAX:
        return False, "", "email_invalid"

    # An internationalized address is rejected up front with a clear,
    # specific reason (the transport cannot carry it).
    if not _is_ascii(addr):
        return False, "", "email_intl_unsupported"

    if _HAVE_EV:
        try:
            r = _ev_validate(addr, check_deliverability=False,
                             allow_smtputf8=False)
            normalized = getattr(r, "normalized", None) or getattr(r, "email", addr)
            return True, normalized, None
        except _EVError:
            return False, "", "email_invalid"

    # Fallback: conservative ASCII pattern.
    if _FALLBACK_RE.match(addr):
        return True, addr, None
    return False, "", "email_invalid"


def sanitize_text(s: str) -> str:
    """Normalize newlines and strip control characters other than
    tab/newline. Does NOT truncate or reflow — content is preserved."""
    if not s:
        return ""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(ch for ch in s if ch == "\n" or ch == "\t"
                   or (ord(ch) >= 32 and ord(ch) != 127))


def validate_submission(to_addr, reply_to, subject, body):
    """Validate a full submission.

    Returns (ok, cleaned, errors) where cleaned is a dict of normalized
    fields and errors is a dict of {field: error_key} (empty if ok).
    """
    errors = {}

    ok_to, to_norm, err_to = check_address(to_addr)
    if not ok_to:
        errors["to"] = err_to

    ok_rt, rt_norm, err_rt = check_address(reply_to)
    if not ok_rt:
        # Distinct required-key so the UI can point at the right field.
        errors["reply_to"] = "replyto_required" if err_rt == "email_required" else err_rt

    subject_c = sanitize_text(subject or "").replace("\n", " ").strip()
    if len(subject_c) > SUBJECT_HARD_MAX:
        errors["subject"] = "subject_too_long"

    body_c = sanitize_text(body or "").strip()
    if not body_c:
        errors["body"] = "body_required"
    elif len(body_c) > BODY_HARD_MAX:
        errors["body"] = "body_too_long"

    cleaned = {
        "to": to_norm if ok_to else (to_addr or "").strip(),
        "reply_to": rt_norm if ok_rt else (reply_to or "").strip(),
        "subject": subject_c,
        "body": body_c,
    }
    return (not errors), cleaned, errors


def wrap_body(body: str, width: int = WRAP_WIDTH) -> str:
    """Reflow a plain-text body so no generated line exceeds `width`
    characters (spec section 5). Wraps on whitespace; over-long unbroken
    tokens (e.g. long URLs) are hard-broken to honor the limit. Blank
    lines between paragraphs are preserved."""
    out_lines = []
    for para in sanitize_text(body).split("\n"):
        if not para.strip():
            out_lines.append("")
            continue
        wrapped = textwrap.wrap(
            para, width=width,
            break_long_words=True, break_on_hyphens=False,
        )
        out_lines.extend(wrapped or [""])
    return "\n".join(out_lines)


def max_line_length(text: str) -> int:
    return max((len(line) for line in text.split("\n")), default=0)
