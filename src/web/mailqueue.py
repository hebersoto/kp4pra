"""
KP4PRA TNC - Web Email holding queue.

Public Web Email Interface submissions are NOT transmitted immediately.
They are placed in a persistent holding queue for trustee review
(WEBMAIL spec section 9). Storage is one JSON file per message under
paths.data/mailq/ (i.e. /rw/kp4pra-tnc/data/mailq), which is persistent
across reboots and is not a volatile tmpfs. Writes are atomic
(tmp + fsync + rename), matching the config_writer pattern.

No new dependencies. No shell execution. Message IDs are generated
server-side and validated on every lookup to prevent path traversal.
"""

import json
import os
import re
import time
import uuid

from common.config import load_config

# Message lifecycle (spec section 9).
STATES = ("Holding", "Approved", "Sending", "Sent", "Failed", "Rejected")
INITIAL_STATE = "Holding"

# Message IDs look like 20260723T104530Z-1a2b3c4d — timestamp (sortable)
# plus a short random suffix. This character set is the ONLY thing we
# ever accept as an on-disk filename stem.
_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")


def _queue_dir() -> str:
    d = os.path.join(load_config()["paths"]["data"], "mailq")
    os.makedirs(d, exist_ok=True)
    return d


def _safe_id(mid: str) -> bool:
    return isinstance(mid, str) and bool(_ID_RE.match(mid))


def _path(mid: str) -> str:
    if not _safe_id(mid):
        raise ValueError("invalid message id")
    return os.path.join(_queue_dir(), mid + ".json")


def new_id() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]


def _atomic_write(path: str, obj: dict):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def enqueue(to_addr: str, reply_to: str, subject: str, body: str,
            lang: str = "en") -> dict:
    """Create a new held message and return its record."""
    mid = new_id()
    now = int(time.time())
    rec = {
        "id": mid,
        "created": now,
        "created_iso": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(now)),
        "to": to_addr,
        "reply_to": reply_to,
        "subject": subject,
        "body": body,
        "lang": lang if lang in ("en", "es") else "en",
        "status": INITIAL_STATE,
        "route": None,       # set at approval/delivery time (Phase 4)
        "attempts": 0,       # delivery attempts (Phase 4)
        "error": None,       # last delivery error (Phase 4)
    }
    _atomic_write(_path(mid), rec)
    return rec


def get(mid: str):
    """Return a message record, or None if missing / bad id."""
    if not _safe_id(mid):
        return None
    try:
        with open(_path(mid), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def list_messages(status: str = None) -> list:
    """Return all message records (optionally filtered by status),
    newest first."""
    out = []
    d = _queue_dir()
    for name in os.listdir(d):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name), "r", encoding="utf-8") as f:
                rec = json.load(f)
        except (ValueError, OSError):
            continue
        if status is None or rec.get("status") == status:
            out.append(rec)
    out.sort(key=lambda r: r.get("created", 0), reverse=True)
    return out


def update(mid: str, **fields) -> dict:
    """Merge fields into a message and persist. Returns the updated
    record, or None if the message is missing. Guards status values."""
    rec = get(mid)
    if rec is None:
        return None
    if "status" in fields and fields["status"] not in STATES:
        raise ValueError("invalid status: %r" % (fields["status"],))
    rec.update(fields)
    _atomic_write(_path(mid), rec)
    return rec


def set_status(mid: str, status: str, **extra) -> dict:
    return update(mid, status=status, **extra)


def delete(mid: str) -> bool:
    """Permanently remove a message file. Returns True if removed."""
    if not _safe_id(mid):
        return False
    try:
        os.remove(_path(mid))
        return True
    except FileNotFoundError:
        return False


def counts() -> dict:
    """Return a {state: n} summary plus 'total'."""
    c = {s: 0 for s in STATES}
    total = 0
    for rec in list_messages():
        total += 1
        st = rec.get("status")
        if st in c:
            c[st] += 1
    c["total"] = total
    return c
