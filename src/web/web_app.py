"""
KP4PRA TNC - Web Management Interface
FastAPI application providing dashboard, Bluetooth management,
provisioning wizard, service control, and configuration editor.

Binds to LAN (configurable). Optional HTTP Basic auth.
All backend actions are strictly allowlisted.
No persistent logging. No shell command injection possible.
Shows live status only - never reads stored logs.
"""

import asyncio
import os
import sys
import json
import secrets
import hashlib
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Depends, Form, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials

# Add parent (src) and this dir (src/web) to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common.config import load_config
from common.config_writer import save_config, validate_config_updates
from common.runtime_status import (
    read_status, write_status, is_reboot_pending,
    set_provisioning_status, clear_provisioning_status, set_reboot_pending
)
from common.bluez_manager import (
    get_adapter_info, get_paired_devices, get_trusted_devices,
    get_connected_devices, get_device_info,
    set_power, set_discoverable, set_pairable,
    pair_device, trust_device, untrust_device, remove_device,
    verify_bluez_state_written, scan_devices, disconnect_device
)
from common.fs_manager import (
    get_filesystem_status, can_perform_permanent_provisioning,
    remount_root_rw, remount_root_ro, sync_storage, schedule_reboot,
    restart_service, stop_service, get_service_status,
    get_volatile_service_log, check_direwolf_tcp, check_dns_sd
)
import auth
import mailqueue
import mailvalidate
import mail_i18n
import b2f
import mailbuilder

PRODUCT_NAME = "KP4PRA TNC"

app = FastAPI(title=PRODUCT_NAME, docs_url=None, redoc_url=None, openapi_url=None)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

def _read_version():
    for p in ("/opt/kp4pra-tnc/VERSION", __file__.rsplit("/src/", 1)[0] + "/VERSION"):
        try:
            return open(p).read().strip()
        except Exception:
            continue
    return "dev"

APP_VERSION = _read_version()
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["dashboard_secured"] = auth.dashboard_password_set
templates.env.globals["station_callsign_valid"] = auth.callsign_valid


def _mail_counts() -> dict:
    """Queue counts by state, safe against a missing/unreadable queue so
    page rendering never breaks."""
    try:
        return mailqueue.counts()
    except Exception:
        base = {s: 0 for s in mailqueue.STATES}
        base["total"] = 0
        return base


# Nav badge = messages awaiting trustee review (Holding).
templates.env.globals["mail_held_count"] = lambda: _mail_counts().get("Holding", 0)



_config = None

def get_config():
    global _config
    if _config is None:
        _config = load_config()
    return _config


# ─────────────────────────────────────────────────────────────────────────────
# Admin Dashboard session authentication
#
# Auth is enforced ONLY once a dashboard password has been configured
# (auth.auth_required). Before that, the dashboard is open so the trustee
# can perform initial station configuration and set a password. The public
# Web Email Interface (/mail*) is never gated here.
# ─────────────────────────────────────────────────────────────────────────────

class NeedsLogin(Exception):
    def __init__(self, next_path: str):
        self.next_path = next_path


@app.exception_handler(NeedsLogin)
async def _needs_login_handler(request: Request, exc: NeedsLogin):
    return RedirectResponse(url=f"/admin/login?next={exc.next_path}", status_code=303)


def require_auth(request: Request):
    """FastAPI dependency: allow the request through if the dashboard is
    not yet secured, or if a valid session cookie is present. Otherwise
    redirect page requests to the login form and 401 API requests."""
    cfg = get_config()
    if not auth.auth_required(cfg):
        return True
    token = request.cookies.get(auth.SESSION_COOKIE, "")
    if auth.session_valid(token):
        return True
    path = request.url.path
    if path.startswith("/api/"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication required")
    raise NeedsLogin(path)


def _set_session_cookies(resp):
    resp.set_cookie(auth.SESSION_COOKIE, auth.issue_session(),
                    max_age=auth.SESSION_TTL, httponly=True,
                    samesite="lax", path="/")
    resp.set_cookie(auth.CSRF_COOKIE, auth.issue_csrf(),
                    max_age=auth.SESSION_TTL, httponly=False,
                    samesite="lax", path="/")


def _clear_session_cookies(resp):
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    resp.delete_cookie(auth.CSRF_COOKIE, path="/")


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    """Enforce the CSRF double-submit token on unsafe methods once the
    dashboard is secured. The login form and the public mail interface
    are exempt (they have no session yet / are handled separately)."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        path = request.url.path
        exempt = path == "/admin/login" or path.startswith("/mail")
        if not exempt and auth.auth_required(get_config()):
            cookie = request.cookies.get(auth.CSRF_COOKIE, "")
            header = request.headers.get("X-CSRF-Token", "")
            if not auth.csrf_ok(cookie, header):
                return JSONResponse({"success": False,
                                     "message": "CSRF validation failed"},
                                    status_code=403)
    return await call_next(request)


# Backwards-compatible alias: existing route signatures use check_auth.
check_auth = require_auth


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build full live status dict (never reads logs)
# ─────────────────────────────────────────────────────────────────────────────

def get_live_status() -> dict:
    cfg = get_config()
    dw = cfg["direwolf"]

    return {
        "product_name": PRODUCT_NAME,
        "adapter": get_adapter_info(),
        "paired_devices": get_paired_devices(),
        "trusted_devices": get_trusted_devices(),
        "connected_devices": get_connected_devices(),
        "rfcomm_bridge": get_service_status("kp4pra-tnc-rfcomm.service"),
        "ble_bridge": get_service_status("kp4pra-tnc-ble.service"),
        "rms_gateway": get_service_status("kp4pra-tnc-rms.service"),
        "ble_runtime": read_status("ble"),
        "rfcomm_runtime": read_status("rfcomm"),
        "direwolf_tcp": check_direwolf_tcp(dw["host"], dw["port"]),
        "dns_sd": check_dns_sd(),
        "filesystem": get_filesystem_status(),
        "reboot_pending": is_reboot_pending(),
        "reboot_info": read_status("reboot_pending"),
        "provisioning": read_status("provisioning"),
        "config": {
            "dw_host": dw["host"],
            "dw_port": dw["port"],
            "ble_name": cfg["ble"]["device_name"],
            "rfcomm_name": cfg["rfcomm"]["device_name"],
            "dns_sd_name": cfg["dns_sd"]["instance_name"],
            "web_port": cfg["web"]["port"],
            "bluez_strategy": cfg["bluetooth"]["bluez_state_strategy"],
            "rms_enabled": cfg["rms"]["enabled"],
            "rms_call": cfg["rms"]["cms_call"],
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Page routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    # Public landing page: Web Email Interface vs Admin Dashboard.
    return templates.TemplateResponse("landing.html", {
        "request": request,
        "product_name": PRODUCT_NAME,
    })


@app.get("/admin", response_class=HTMLResponse)
async def dashboard(request: Request, _auth=Depends(check_auth)):
    status_data = get_live_status()
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "product_name": PRODUCT_NAME,
        "status": status_data,
        "mail_counts": _mail_counts(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Public Web Email Interface (no Dashboard authentication)
#
# Submissions are validated and placed in a persistent holding queue for
# trustee review; they are NEVER transmitted here (spec sections 4-9, 15).
# Form input arrives as JSON and is parsed without python-multipart.
# ─────────────────────────────────────────────────────────────────────────────

MAIL_CSRF_COOKIE = "kp4pra_mail_csrf"


def _webmail_enabled(cfg=None) -> bool:
    cfg = cfg or get_config()
    return bool(cfg.get("webmail", {}).get("enabled", True))


@app.get("/mail", response_class=HTMLResponse)
async def mail_compose(request: Request, lang: str = "en"):
    lang = mail_i18n.normalize_lang(lang)
    t = mail_i18n.get_strings(lang)
    token = auth.issue_csrf()
    resp = templates.TemplateResponse("mail/compose.html", {
        "request": request,
        "product_name": PRODUCT_NAME,
        "t": t,
        "enabled": _webmail_enabled(),
        "csrf_token": token,
        "prev": {},
    })
    # Public double-submit token: readable cookie echoed by the composer.
    resp.set_cookie(MAIL_CSRF_COOKIE, token, max_age=3600,
                    httponly=False, samesite="lax", path="/mail")
    return resp


@app.post("/mail/submit")
async def mail_submit(request: Request):
    cfg = get_config()
    if not _webmail_enabled(cfg):
        return JSONResponse({"success": False, "message": "unavailable"},
                            status_code=503)

    try:
        body = await request.json()
    except Exception:
        body = {}

    lang = mail_i18n.normalize_lang(body.get("lang", "en"))
    t = mail_i18n.get_strings(lang)

    # Public CSRF (double-submit) check.
    cookie_tok = request.cookies.get(MAIL_CSRF_COOKIE, "")
    form_tok = body.get("csrf", "")
    if not auth.csrf_ok(cookie_tok, form_tok):
        return JSONResponse({"success": False, "message": t["csrf_error"]},
                            status_code=403)

    ok, cleaned, errors = mailvalidate.validate_submission(
        body.get("to"), body.get("reply_to"),
        body.get("subject"), body.get("body"))

    if not ok:
        # Map stable error keys to localized text for each bad field.
        localized = {field: t.get(key, t["email_invalid"])
                     for field, key in errors.items()}
        return JSONResponse({"success": False, "errors": localized},
                            status_code=400)

    try:
        mailqueue.enqueue(cleaned["to"], cleaned["reply_to"],
                          cleaned["subject"], cleaned["body"], lang)
    except Exception:
        return JSONResponse({"success": False, "message": t["queue_error"]},
                            status_code=500)

    return JSONResponse({"success": True, "redirect": f"/mail/sent?lang={lang}"})


@app.get("/mail/sent", response_class=HTMLResponse)
async def mail_sent(request: Request, lang: str = "en"):
    lang = mail_i18n.normalize_lang(lang)
    return templates.TemplateResponse("mail/confirm.html", {
        "request": request,
        "product_name": PRODUCT_NAME,
        "t": mail_i18n.get_strings(lang),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Admin: Web Email message management (spec section 10). Authenticated.
#
# Approve : Holding/Rejected/Failed -> Approved (queued for Phase 4 delivery).
# Reject  : Holding/Approved/Failed -> Rejected (kept on disk for the record).
# Delete  : permanently removes the file (requires explicit confirmation).
# ─────────────────────────────────────────────────────────────────────────────

_MSG_ACTIONS = {"approve", "reject", "delete"}
_APPROVE_FROM = {"Holding", "Rejected", "Failed"}
_REJECT_FROM = {"Holding", "Approved", "Failed"}


@app.get("/admin/messages", response_class=HTMLResponse)
async def messages_list(request: Request, status: str = None,
                        _auth=Depends(check_auth)):
    flt = status if status in mailqueue.STATES else None
    msgs = mailqueue.list_messages(flt)
    return templates.TemplateResponse("messages.html", {
        "request": request,
        "product_name": PRODUCT_NAME,
        "messages": msgs,
        "counts": _mail_counts(),
        "filter": flt,
        "states": list(mailqueue.STATES),
    })


@app.get("/admin/messages/{mid}", response_class=HTMLResponse)
async def message_detail(request: Request, mid: str,
                         _auth=Depends(check_auth)):
    rec = mailqueue.get(mid)
    if rec is None:
        return RedirectResponse(url="/admin/messages", status_code=303)
    return templates.TemplateResponse("message_detail.html", {
        "request": request,
        "product_name": PRODUCT_NAME,
        "m": rec,
    })


@app.post("/api/messages/action")
async def api_messages_action(request: Request, _auth=Depends(check_auth)):
    body = await request.json()
    action = body.get("action")
    ids = body.get("ids") or []
    confirm = bool(body.get("confirm", False))

    if action not in _MSG_ACTIONS:
        return JSONResponse({"success": False, "message": "Unknown action."},
                            status_code=400)
    if not isinstance(ids, list) or not ids:
        return JSONResponse({"success": False, "message": "No messages selected."},
                            status_code=400)
    if action == "delete" and not confirm:
        return JSONResponse({"success": False, "need_confirm": True,
                             "message": "Confirmation required to delete."},
                            status_code=400)

    done = 0
    skipped = 0
    for mid in ids:
        rec = mailqueue.get(mid)
        if rec is None:
            skipped += 1
            continue
        st = rec.get("status")
        if action == "approve":
            if st in _APPROVE_FROM:
                mailqueue.set_status(mid, "Approved")
                done += 1
            else:
                skipped += 1
        elif action == "reject":
            if st in _REJECT_FROM:
                mailqueue.set_status(mid, "Rejected")
                done += 1
            else:
                skipped += 1
        elif action == "delete":
            if mailqueue.delete(mid):
                done += 1
            else:
                skipped += 1

    return JSONResponse({"success": True, "action": action,
                         "done": done, "skipped": skipped,
                         "counts": _mail_counts()})


@app.post("/api/messages/test")
async def api_messages_test(request: Request, _auth=Depends(check_auth)):
    """DRY-RUN delivery test for one message (spec Phase 4, step 3).

    Assembles the full CMS-path B2F exchange and returns a transcript.
    Opens no socket and transmits nothing. Does NOT change the message
    status -- a dry run is a diagnostic, not a delivery."""
    body = await request.json()
    mid = body.get("id", "")
    rec = mailqueue.get(mid)
    if rec is None:
        return JSONResponse({"success": False, "message": "Message not found."},
                            status_code=404)

    cfg = get_config()
    method = cfg.get("webmail", {}).get("delivery", {}).get("method", "cms")
    if method != "cms":
        return JSONResponse({"success": False,
            "message": "Only the CMS dry-run is available so far."},
            status_code=400)

    try:
        transcript = b2f.dry_run_cms(rec, cfg, version=APP_VERSION)
    except mailbuilder.BuildError as e:
        return JSONResponse({"success": False,
            "message": "Cannot build message: %s" % e}, status_code=400)
    except Exception as e:
        return JSONResponse({"success": False,
            "message": "Dry run failed: %s" % e}, status_code=500)

    # Attach the latest test result to the record (status unchanged).
    mailqueue.update(mid, last_test={
        "at": transcript["at"], "method": transcript["method"],
        "compressed_size": transcript["compressed_size"],
        "uncompressed_size": transcript["uncompressed_size"],
        "proposal_checksum": transcript["proposal_checksum"],
    })

    return JSONResponse({"success": True, "dry_run": True,
                         "transcript": transcript})


# ─────────────────────────────────────────────────────────────────────────────
# Admin Dashboard login / logout
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/admin"):
    if not auth.dashboard_password_set(get_config()):
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "product_name": PRODUCT_NAME,
        "next": auth.safe_next(next),
        "error": False,
    })


@app.post("/admin/login")
async def login_submit(request: Request):
    from urllib.parse import parse_qs
    raw = (await request.body()).decode("utf-8", "replace")
    data = parse_qs(raw, keep_blank_values=True)
    password = (data.get("password") or [""])[0]
    next = (data.get("next") or ["/admin"])[0]
    cfg = get_config()
    stored = cfg["web"].get("dashboard_password_hash", "")
    if not stored or not auth.verify_password(password, stored):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "product_name": PRODUCT_NAME,
            "next": auth.safe_next(next),
            "error": True,
        }, status_code=status.HTTP_401_UNAUTHORIZED)
    resp = RedirectResponse(url=auth.safe_next(next), status_code=303)
    _set_session_cookies(resp)
    return resp


@app.get("/admin/logout")
async def logout(request: Request):
    resp = RedirectResponse(url="/", status_code=303)
    _clear_session_cookies(resp)
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# Admin Dashboard password (set / change)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/dashboard/password")
async def api_dashboard_password(request: Request, _auth=Depends(check_auth)):
    body = await request.json()
    new_pw = body.get("new_password", "")
    current_pw = body.get("current_password", "")
    cfg = get_config()

    if not auth.callsign_valid(cfg):
        return JSONResponse({"success": False,
            "message": "Configure a valid station callsign and save before "
                       "setting a Dashboard password."}, status_code=400)

    if auth.dashboard_password_set(cfg):
        stored = cfg["web"]["dashboard_password_hash"]
        if not auth.verify_password(current_pw, stored):
            return JSONResponse({"success": False,
                "message": "Current password is incorrect."}, status_code=403)

    ok, msg = auth.validate_new_password(new_pw)
    if not ok:
        return JSONResponse({"success": False, "message": msg}, status_code=400)

    fs = get_filesystem_status()
    if not fs["rw_partition_writable"]:
        return JSONResponse({"success": False,
            "message": "Configuration path is not writable. Check "
                       "/rw/kp4pra-tnc/ mount."}, status_code=503)

    save_ok, save_msg = save_config(
        {"web": {"dashboard_password_hash": auth.hash_password(new_pw)}})
    if not save_ok:
        return JSONResponse({"success": False, "message": save_msg},
                            status_code=500)

    global _config
    _config = None
    return JSONResponse({"success": True,
        "message": "Dashboard password saved. Authentication is now required "
                   "for the Admin Dashboard."})


@app.get("/bluetooth", response_class=HTMLResponse)
async def bluetooth_page(request: Request, _auth=Depends(check_auth)):
    status_data = get_live_status()
    return templates.TemplateResponse("bluetooth.html", {
        "request": request,
        "product_name": PRODUCT_NAME,
        "status": status_data,
    })


@app.get("/provision/android", response_class=HTMLResponse)
async def android_provision_page(request: Request, _auth=Depends(check_auth)):
    status_data = get_live_status()
    return templates.TemplateResponse("provision_android.html", {
        "request": request,
        "product_name": PRODUCT_NAME,
        "status": status_data,
    })


@app.get("/provision/iphone", response_class=HTMLResponse)
async def iphone_provision_page(request: Request, _auth=Depends(check_auth)):
    status_data = get_live_status()
    return templates.TemplateResponse("provision_iphone.html", {
        "request": request,
        "product_name": PRODUCT_NAME,
        "status": status_data,
    })


@app.get("/services", response_class=HTMLResponse)
async def services_page(request: Request, _auth=Depends(check_auth)):
    status_data = get_live_status()
    rfcomm_log = get_volatile_service_log("kp4pra-tnc-rfcomm.service")
    ble_log = get_volatile_service_log("kp4pra-tnc-ble.service")
    return templates.TemplateResponse("services.html", {
        "request": request,
        "product_name": PRODUCT_NAME,
        "status": status_data,
        "rfcomm_log": rfcomm_log,
        "ble_log": ble_log,
    })


@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request, _auth=Depends(check_auth)):
    cfg = get_config()
    fs = get_filesystem_status()
    return templates.TemplateResponse("config.html", {
        "request": request,
        "product_name": PRODUCT_NAME,
        "config": cfg,
        "filesystem": fs,
        "reboot_pending": is_reboot_pending(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# API: Live status (JSON)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/version")
async def api_version():
    return JSONResponse({"version": APP_VERSION})


@app.get("/api/status")
async def api_status(_auth=Depends(check_auth)):
    return JSONResponse(get_live_status())


@app.get("/api/adapter")
async def api_adapter(_auth=Depends(check_auth)):
    return JSONResponse(get_adapter_info())


@app.get("/api/devices")
async def api_devices(_auth=Depends(check_auth)):
    return JSONResponse({
        "paired": get_paired_devices(),
        "trusted": get_trusted_devices(),
        "connected": get_connected_devices(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# API: Runtime Bluetooth actions (no reboot required)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/bt/power")
async def api_bt_power(request: Request, _auth=Depends(check_auth)):
    body = await request.json()
    on = bool(body.get("on", True))
    ok = set_power(on)
    return JSONResponse({"success": ok, "message": "Power on" if on else "Power off"})


@app.post("/api/bt/discoverable")
async def api_bt_discoverable(request: Request, _auth=Depends(check_auth)):
    body = await request.json()
    on = bool(body.get("on", True))
    timeout = int(body.get("timeout", 180))
    ok = set_discoverable(on, timeout)
    return JSONResponse({"success": ok})


@app.post("/api/bt/pairable")
async def api_bt_pairable(request: Request, _auth=Depends(check_auth)):
    body = await request.json()
    on = bool(body.get("on", True))
    timeout = int(body.get("timeout", 180))
    ok = set_pairable(on, timeout)
    return JSONResponse({"success": ok})


@app.post("/api/bt/scan")
async def api_bt_scan(_auth=Depends(check_auth)):
    """
    Start a Bluetooth scan. Results are runtime-only, not saved to disk.
    """
    write_status("scan", {"scanning": True, "devices": []})
    devices = await scan_devices(duration=10)
    # Store scan results in runtime status only (disappears after reboot)
    write_status("scan", {"scanning": False, "devices": devices})
    return JSONResponse({"success": True, "devices": devices})


@app.get("/api/bt/scan/status")
async def api_scan_status(_auth=Depends(check_auth)):
    return JSONResponse(read_status("scan"))


@app.post("/api/bt/disconnect")
async def api_bt_disconnect(request: Request, _auth=Depends(check_auth)):
    body = await request.json()
    address = body.get("address", "")
    ok, msg = disconnect_device(address)
    return JSONResponse({"success": ok, "message": msg})


# ─────────────────────────────────────────────────────────────────────────────
# API: Permanent Bluetooth provisioning (requires remount + reboot)
# ─────────────────────────────────────────────────────────────────────────────

async def _permanent_provisioning_workflow(action_fn, address: str, action_name: str) -> dict:
    """
    Controlled maintenance workflow for permanent Bluetooth operations:
    1. Verify filesystem can be made writable
    2. Stop bridge services
    3. Remount rw (if needed)
    4. Perform action
    5. Verify BlueZ state written
    6. Sync
    7. Remount ro
    8. Schedule reboot
    """
    set_provisioning_status("start", f"Starting {action_name} for {address}")

    # Step 1: Check if we can proceed
    can_proceed, reason = can_perform_permanent_provisioning()
    if not can_proceed:
        set_provisioning_status("failed", reason, success=False, error=reason)
        return {"success": False, "message": reason, "step": "preflight"}

    fs = get_filesystem_status()
    needs_remount = not fs["bluez_state_writable"]

    # Step 2: Stop bridge services
    set_provisioning_status("stopping_services", "Stopping bridge services")
    stop_service("kp4pra-tnc-rfcomm.service")

    # Step 3: Remount rw if needed
    if needs_remount:
        set_provisioning_status("remounting_rw", "Remounting filesystem read/write")
        ok, msg = remount_root_rw()
        if not ok:
            set_provisioning_status("failed", msg, success=False, error=msg)
            restart_service("kp4pra-tnc-rfcomm.service")
            return {"success": False, "message": msg, "step": "remount_rw"}

    # Step 4: Perform the Bluetooth action
    set_provisioning_status("performing_action", f"Performing {action_name}")
    try:
        ok, msg = action_fn(address)
        if ok and action_name == "pair_device":
            t_ok, t_msg = trust_device(address)
            msg = f"{msg}; trust: {t_msg}"
    except Exception as e:
        ok, msg = False, str(e)

    # Step 4b: Restore group read access (BlueZ recreates bond files root:root)
    try:
        import subprocess as _sp
        _sp.run(["sudo", "/usr/local/bin/kp4pra-fix-bt-perms"],
                capture_output=True, timeout=10)
    except Exception as e:
        print(f"[KP4PRA TNC] bt-perms fix failed (non-fatal): {e}", flush=True)

    # Step 5: Verify BlueZ state if it was a pair/trust operation
    if ok and action_name in ("pair_device", "trust_device"):
        written = verify_bluez_state_written(address)
        if not written:
            ok = False
            msg = "BlueZ did not write persistent state - pairing may not survive reboot"

    # Step 6: Sync
    set_provisioning_status("syncing", "Syncing storage")
    sync_storage()

    # Step 7: Remount ro
    if needs_remount:
        set_provisioning_status("remounting_ro", "Remounting filesystem read-only")
        remount_root_ro()  # Best effort; always attempt

    if not ok:
        set_provisioning_status("failed", msg, success=False, error=msg)
        restart_service("kp4pra-tnc-rfcomm.service")
        return {"success": False, "message": msg, "step": "action"}

    # Step 8: Schedule reboot - only needed when we had to remount
    if not needs_remount:
        final_msg = f"{action_name} completed and saved. No reboot needed (filesystem was writable)."
        set_provisioning_status("complete", final_msg, success=True)
        restart_service("kp4pra-tnc-rfcomm.service")
        return {"success": True, "message": final_msg, "reboot_scheduled": False, "step": "complete"}

    set_provisioning_status("rebooting", "Scheduling reboot")
    set_reboot_pending(f"Permanent Bluetooth provisioning: {action_name}")
    reboot_ok, reboot_msg = schedule_reboot(delay=5)

    final_msg = (
        f"{action_name} completed. "
        f"KP4PRA TNC will reboot to apply the permanent configuration."
    )
    set_provisioning_status("complete", final_msg, success=True)

    return {
        "success": True,
        "message": final_msg,
        "reboot_scheduled": reboot_ok,
        "step": "complete",
    }


@app.post("/api/bt/pair")
async def api_bt_pair(request: Request, _auth=Depends(check_auth)):
    body = await request.json()
    address = body.get("address", "")
    if not body.get("confirmed", False):
        return JSONResponse({"success": False, "message": "Confirmation required"}, status_code=400)
    result = await _permanent_provisioning_workflow(pair_device, address, "pair_device")
    return JSONResponse(result)


@app.post("/api/bt/trust")
async def api_bt_trust(request: Request, _auth=Depends(check_auth)):
    body = await request.json()
    address = body.get("address", "")
    if not body.get("confirmed", False):
        return JSONResponse({"success": False, "message": "Confirmation required"}, status_code=400)
    result = await _permanent_provisioning_workflow(trust_device, address, "trust_device")
    return JSONResponse(result)


@app.post("/api/bt/untrust")
async def api_bt_untrust(request: Request, _auth=Depends(check_auth)):
    body = await request.json()
    address = body.get("address", "")
    if not body.get("confirmed", False):
        return JSONResponse({"success": False, "message": "Confirmation required"}, status_code=400)
    result = await _permanent_provisioning_workflow(untrust_device, address, "untrust_device")
    return JSONResponse(result)


@app.post("/api/bt/remove")
async def api_bt_remove(request: Request, _auth=Depends(check_auth)):
    body = await request.json()
    address = body.get("address", "")
    if not body.get("confirmed", False):
        return JSONResponse({"success": False, "message": "Confirmation required"}, status_code=400)
    result = await _permanent_provisioning_workflow(remove_device, address, "remove_device")
    return JSONResponse(result)


# ─────────────────────────────────────────────────────────────────────────────
# API: Service control
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/wifi/status")
async def api_wifi_status(_auth=Depends(check_auth)):
    import subprocess
    try:
        r = subprocess.run(["/usr/local/bin/kp4pra-wifi-mode", "status"],
                           capture_output=True, timeout=10)
        mode = r.stdout.decode(errors="replace").strip() or "unknown"
    except Exception as e:
        mode = f"error: {e}"
    cfg = load_config()
    return JSONResponse({
        "mode": mode,
        "client_ssid": cfg.get("wifi", {}).get("client_ssid", ""),
    })


@app.post("/api/wifi/mode")
async def api_wifi_mode(request: Request, _auth=Depends(check_auth)):
    import subprocess
    body = await request.json()
    mode = body.get("mode", "")
    if mode not in ("ap", "client"):
        return JSONResponse({"success": False, "message": "mode must be 'ap' or 'client'"}, status_code=400)
    if mode == "client":
        cfg = load_config()
        if not cfg.get("wifi", {}).get("client_ssid", "").strip():
            return JSONResponse({"success": False,
                "message": "No client WiFi configured. Set a Client WiFi SSID on the Config page first."}, status_code=400)
    try:
        r = subprocess.run(["sudo", "/usr/local/bin/kp4pra-wifi-mode", mode],
                           capture_output=True, timeout=45)
        out = r.stdout.decode(errors="replace").strip()
        if r.returncode == 0:
            return JSONResponse({"success": True, "message": out or f"Switched to {mode} mode"})
        err = r.stderr.decode(errors="replace").strip()
        return JSONResponse({"success": False, "message": err or out or "wifi-mode failed"}, status_code=500)
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/service/restart")
async def api_service_restart(request: Request, _auth=Depends(check_auth)):
    body = await request.json()
    svc = body.get("service", "")
    ok, msg = restart_service(svc)
    return JSONResponse({"success": ok, "message": msg})


@app.post("/api/service/stop")
async def api_service_stop(request: Request, _auth=Depends(check_auth)):
    body = await request.json()
    svc = body.get("service", "")
    ok, msg = stop_service(svc)
    return JSONResponse({"success": ok, "message": msg})


@app.get("/api/service/{service_name}/status")
async def api_service_status(service_name: str, _auth=Depends(check_auth)):
    # Normalize name
    if not service_name.endswith(".service"):
        service_name += ".service"
    return JSONResponse(get_service_status(service_name))


@app.get("/api/service/{service_name}/log")
async def api_service_log(service_name: str, lines: int = 30, _auth=Depends(check_auth)):
    if not service_name.endswith(".service"):
        service_name += ".service"
    log = get_volatile_service_log(service_name, lines=lines)
    return JSONResponse({"log": log, "volatile_only": True})


# ─────────────────────────────────────────────────────────────────────────────
# API: Configuration save (permanent, may require reboot)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/config/save")
async def api_config_save(request: Request, _auth=Depends(check_auth)):
    body = await request.json()
    updates = body.get("config", {})
    confirmed = body.get("confirmed", False)

    if not confirmed:
        return JSONResponse({"success": False, "message": "Confirmation required"}, status_code=400)

    valid, err = validate_config_updates(updates)
    if not valid:
        return JSONResponse({"success": False, "message": err}, status_code=400)

    fs = get_filesystem_status()
    if not fs["rw_partition_writable"]:
        return JSONResponse({
            "success": False,
            "message": "Configuration path is not writable. Check /rw/kp4pra-tnc/ mount."
        }, status_code=503)

    ok, msg = save_config(updates)
    if not ok:
        return JSONResponse({"success": False, "message": msg}, status_code=500)

    # Reload config cache
    global _config
    _config = None

    return JSONResponse({
        "success": True,
        "message": f"Configuration saved. Some changes require a service restart or reboot.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# API: Direwolf configuration (Station Information -> direwolf.conf)
# ─────────────────────────────────────────────────────────────────────────────

from common.direwolf_conf import (
    detect_sound_cards, detect_cm108_adevice,
    generate_direwolf_conf, write_direwolf_conf, restart_direwolf,
    DIREWOLF_CONF_PATH,
)


@app.get("/api/soundcards")
async def api_soundcards(_auth=Depends(check_auth)):
    return JSONResponse({
        "cards": detect_sound_cards(),
        "cm108_adevice": detect_cm108_adevice(),
    })


@app.post("/api/direwolf/preview")
async def api_direwolf_preview(request: Request, _auth=Depends(check_auth)):
    body = await request.json()
    station = body.get("station") or load_config().get("station", {})
    text = generate_direwolf_conf(station)
    import os as _os
    target = _os.path.realpath(DIREWOLF_CONF_PATH)
    writable = _os.access(_os.path.dirname(target), _os.W_OK) or \
               (_os.path.exists(target) and _os.access(target, _os.W_OK))
    return JSONResponse({"success": True, "conf": text,
                         "path": DIREWOLF_CONF_PATH, "writable": writable})


@app.post("/api/direwolf/apply")
async def api_direwolf_apply(request: Request, _auth=Depends(check_auth)):
    body = await request.json()
    if not body.get("confirmed", False):
        return JSONResponse({"success": False, "message": "Confirmation required"}, status_code=400)
    station = body.get("station") or load_config().get("station", {})
    try:
        text = generate_direwolf_conf(station)
        ok, msg = write_direwolf_conf(text)
        if not ok:
            return JSONResponse({"success": False, "message": msg})
        r_ok, r_msg = (True, "Restart skipped") if not body.get("restart", True) \
                      else restart_direwolf()
        return JSONResponse({"success": True,
                             "message": f"{msg}. {r_msg}.",
                             "restarted": r_ok, "conf": text})
    except Exception as e:
        return JSONResponse({"success": False, "message": f"Apply error: {e}"}, status_code=200)


# ─────────────────────────────────────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────────────────────────────────────

def main():
    import uvicorn
    cfg = load_config()
    host = cfg["web"].get("host", "0.0.0.0")
    port = cfg["web"].get("port", 8088)
    print(f"[KP4PRA TNC] Web interface starting on http://{host}:{port}", flush=True)
    uvicorn.run(
        "web_app:app",
        host=host,
        port=port,
        log_level="warning",   # Minimal stdout only; no file logging
        access_log=False,      # No access log to disk
    )


if __name__ == "__main__":
    main()
