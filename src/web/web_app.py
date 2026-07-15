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

# Add parent to path for imports when running directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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



_config = None

def get_config():
    global _config
    if _config is None:
        _config = load_config()
    return _config


# ─────────────────────────────────────────────────────────────────────────────
# Optional HTTP Basic Authentication
# ─────────────────────────────────────────────────────────────────────────────

security = HTTPBasic(auto_error=False)

def check_auth(credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    cfg = get_config()
    if not cfg["web"].get("auth_enabled", False):
        return True
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    expected_user = cfg["web"].get("username", "admin")
    expected_pass = cfg["web"].get("password", "")
    ok_user = secrets.compare_digest(credentials.username, expected_user)
    ok_pass = secrets.compare_digest(credentials.password, expected_pass)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


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
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Page routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _auth=Depends(check_auth)):
    status_data = get_live_status()
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "product_name": PRODUCT_NAME,
        "status": status_data,
    })


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
