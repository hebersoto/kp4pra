"""
KP4PRA TNC - one-shot APRS beacon from the TFT (button 24 held >= 3 s).

Independent of the APRS chat page: it does not need a chat session. The
frame goes to Dire Wolf's AGW port as an unproto UI frame ('V' with the
digipeater path, 'M' when the path is empty), so it does not take one of
Dire Wolf's few KISS client slots and does not disturb RMS/BLE/RFCOMM.

  position known  -> APRS position report  =DDMM.mmN/DDDMM.mmW-comment
  no position     -> APRS status report    >comment   (beacon without position)

Position source is the same as the APRS page: beacon lat/lon, else station
lat/lon, else the station Maidenhead grid.
"""

import socket
import struct

from web import aprs
from tft.status import station_call

AGW_PORT = 8000
AGW_HEADER = struct.Struct("<8B10s10sII")
PID_NO_L3 = 0xF0


def beacon_call(cfg: dict) -> str:
    a = cfg.get("aprs", {}) or {}
    return aprs.normalize_call(a.get("mycall") or station_call(cfg))


def beacon_position(cfg: dict):
    b = ((cfg.get("aprs", {}) or {}).get("beacon", {}) or {})
    st = cfg.get("station", {}) or {}
    lat = b.get("lat") or st.get("lat") or 0
    lon = b.get("lon") or st.get("lon") or 0
    if lat or lon:
        return (float(lat), float(lon))
    return aprs.maidenhead_to_latlon(st.get("mygrid") or "")


def beacon_info(cfg: dict) -> str:
    b = ((cfg.get("aprs", {}) or {}).get("beacon", {}) or {})
    comment = (b.get("comment") or "KP4PRA TNC").replace("\r", " ").replace("\n", " ")
    pos = beacon_position(cfg)
    if pos:
        return aprs.position_report(pos[0], pos[1], b.get("symbol_table", "/"),
                                    b.get("symbol_code", "-"), comment)
    return ">" + comment[:62]


def _call10(call: str) -> bytes:
    return call.encode("ascii")[:9].ljust(10, b"\x00")


def agw_frame(call_from: str, call_to: str, info: str, digis) -> bytes:
    """AGW 'V' (unproto via path) or 'M' (unproto, no path) frame."""
    info_b = info.encode("ascii", errors="replace")
    digis = [d for d in (digis or []) if d][:8]
    if digis:
        data = bytes([len(digis)]) + b"".join(_call10(d) for d in digis) + info_b
        kind = "V"
    else:
        data, kind = info_b, "M"
    header = AGW_HEADER.pack(0, 0, 0, 0, ord(kind), 0, PID_NO_L3, 0,
                             _call10(call_from), _call10(call_to), len(data), 0)
    return header + data


def send_beacon(cfg: dict, host: str = "127.0.0.1", port: int = AGW_PORT) -> str:
    """Send the beacon; returns the info field sent. Raises on socket error."""
    a = cfg.get("aprs", {}) or {}
    info = beacon_info(cfg)
    frame = agw_frame(beacon_call(cfg), aprs.TOCALL, info,
                      a.get("digipath") or [])
    with socket.create_connection((host, port), timeout=5) as s:
        s.sendall(frame)
    return info
