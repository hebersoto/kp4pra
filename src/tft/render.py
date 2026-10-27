"""
KP4PRA TNC - draw the TFT screens (240x240) with Pillow. No hardware here,
so every screen can be rendered to PNG in tests and previews.

Layout: 8 rows of 30 px, DejaVu Sans 24 pt. Lines wider than the screen
scroll slowly to the left (marquee) instead of being cut.

Screen 1                      Screen 2
  1  KP4PRA-10 (callsign)       1  Winlink: 2 pending
  2  traffic (red TX/green RX)  2  WiFi: 3 devices
  3  traffic                    3  BT/BLE: 1
  4  traffic                    4  traffic
  5  current: FROM>TO [via ..] 5  traffic
  6  current: <TOKEN> time TX/RX 6  traffic
  7  current: UI text            7  traffic
  8  AP 172.16.0.1 (static)     8  AP 172.16.0.1 (static)
"""

from PIL import Image, ImageDraw, ImageFont

W = H = 240
ROW_H = 30
ROWS = 8
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_SIZE = 24
SCROLL_PX_PER_S = 40
SCROLL_GAP = 40

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
KP4PRA_GREEN = (0x5B, 0xA9, 0x08)      # --kp4pra-primary on the web pages
RX_GREEN = (0x78, 0xBE, 0x20)          # --kp4pra-primary-alt: brighter on black
TX_RED = (0xFF, 0x30, 0x30)
GREY = (0x9A, 0xA5, 0xAE)
BAR_BG = (0x1F, 0x29, 0x33)
YELLOW = (0xFF, 0xD4, 0x3B)

_fonts = {}


def font(size=FONT_SIZE, bold=False):
    key = (size, bold)
    if key not in _fonts:
        try:
            _fonts[key] = ImageFont.truetype(FONT_BOLD_PATH if bold else FONT_PATH, size)
        except OSError:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]


def _text_w(draw, text, f):
    return draw.textlength(text, font=f)


def _row(draw, row, text, color, t=0.0, f=None, bg=None, x0=2):
    """Draw one row; scroll horizontally if it does not fit."""
    f = f or font()
    y = row * ROW_H
    if bg is not None:
        draw.rectangle([0, y, W - 1, y + ROW_H - 1], fill=bg)
    ty = y + (ROW_H - FONT_SIZE) // 2 - 2
    width = _text_w(draw, text, f)
    avail = W - 2 * x0
    if width <= avail:
        draw.text((x0, ty), text, font=f, fill=color)
        return
    span = width + SCROLL_GAP
    off = int((t * SCROLL_PX_PER_S) % span)
    draw.text((x0 - off, ty), text, font=f, fill=color)
    draw.text((x0 - off + span, ty), text, font=f, fill=color)


def _status_bar(draw, state):
    """Row 8: AP/CL + IP, static. Shrinks the font instead of scrolling."""
    text = "%s %s" % (state.get("mode", "?"), state.get("ip", ""))
    size = FONT_SIZE
    while size > 14 and _text_w(draw, text, font(size)) > W - 4:
        size -= 1
    y = (ROWS - 1) * ROW_H
    draw.rectangle([0, y, W - 1, H - 1], fill=BAR_BG)
    draw.text((2, y + (ROW_H - size) // 2 - 1), text, font=font(size), fill=WHITE)


def _color(frame):
    return TX_RED if frame.direction == "TX" else RX_GREEN


def _traffic(draw, frames, first_row, n_rows, t):
    recent = list(frames)[-n_rows:]
    for i in range(n_rows):
        row = first_row + i
        if i < len(recent):
            fr = recent[i]
            _row(draw, row, fr.short, _color(fr), t)
        elif i == 0 and not recent:
            _row(draw, row, "no traffic yet", GREY, t)


def mode_splash(mode):
    """Same look as the start splash: entering 'AP' or 'CL' WiFi mode."""
    lines = ("WiFi", "Access Point", "Mode") if mode == "AP" else ("WiFi", "Client", "Mode")
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    f = font(30, bold=True)
    top = (H - len(lines) * 44) // 2
    for i, word in enumerate(lines):
        tw = _text_w(d, word, f)
        d.text(((W - tw) / 2, top + i * 44), word, font=f, fill=KP4PRA_GREEN)
    return img


SHUTDOWN_MSG = ("Shutting Down,", "disconnect power", "in 15 seconds")


def shutdown_screen(left=None):
    """Shutdown started from the web UI (or the system is powering off).
    left = seconds until power-off (countdown, big red number), or None for
    the final message: "Shutting Down, disconnect power in 15 seconds"."""
    img = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(img)

    def fit(text, size, bold=True):         # largest font <= size that fits
        while size > 12 and _text_w(d, text, font(size, bold)) > W - 16:
            size -= 1
        return font(size, bold)

    def centered(y, text, f, color):
        d.text(((W - _text_w(d, text, f)) / 2, y), text, font=f, fill=color)

    if left is not None:
        centered(30, "Shutting Down", fit("Shutting Down", 28), WHITE)
        centered(75, str(max(int(left), 0)), font(96, bold=True), TX_RED)
        centered(196, "Cancel on web page", fit("Cancel on web page", 18, False), GREY)
    else:
        size = min(26, *(fit(line, 26).size for line in SHUTDOWN_MSG))
        f = font(size, bold=True)              # same size on all three lines
        for i, line in enumerate(SHUTDOWN_MSG):
            centered(62 + i * 40, line, f, WHITE if i == 0 else YELLOW)
    return img


def _wrap(d, text, f, width):
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if _text_w(d, t, f) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def install_screen(title, message="", footer="", kind="busy"):
    """Setup progress (bin/kp4pra-install-state), also "Restarting".
    kind: busy (yellow title), ok (green), failed (red)."""
    img = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(img)
    color = {"ok": RX_GREEN, "failed": TX_RED}.get(kind, YELLOW)
    size = 28
    while size > 16 and _text_w(d, title, font(size, True)) > W - 16:
        size -= 1
    ft = font(size, True)
    d.text(((W - _text_w(d, title, ft)) / 2, 14), title, font=ft, fill=color)
    fm = font(19)
    lines = _wrap(d, message, fm, W - 16)[:6]
    for i, line in enumerate(lines):
        d.text(((W - _text_w(d, line, fm)) / 2, 58 + i * 24), line, font=fm, fill=WHITE)
    if footer:
        ff = font(17)
        while _text_w(d, footer, ff) > W - 8 and ff.size > 12:
            ff = font(ff.size - 1)
        d.text(((W - _text_w(d, footer, ff)) / 2, 210), footer, font=ff, fill=GREY)
    return img


def splash():
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    f = font(34, bold=True)
    for i, word in enumerate(("KP4PRA", "TNC")):
        tw = _text_w(d, word, f)
        d.text(((W - tw) / 2, 70 + i * 52), word, font=f, fill=KP4PRA_GREEN)
    return img


def screen1(state, t=0.0):
    img = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(img)
    _row(d, 0, state.get("call", ""), WHITE, t, f=font(bold=True))
    d.line([0, ROW_H - 1, W - 1, ROW_H - 1], fill=BAR_BG)
    frames = state.get("frames", [])
    _traffic(d, frames, 1, 3, t)
    d.line([0, 4 * ROW_H - 1, W - 1, 4 * ROW_H - 1], fill=BAR_BG)
    cur = state.get("current")
    if cur:
        c = _color(cur)
        _row(d, 4, cur.route, c, t)
        _row(d, 5, "<%s>  %s %s" % (cur.token, cur.ts[:5], cur.direction), YELLOW, t)
        _row(d, 6, cur.text or "", WHITE, t)
    else:
        _row(d, 4, "waiting for", GREY, t)
        _row(d, 5, "packets...", GREY, t)
    _status_bar(d, state)
    return img


def screen2(state, t=0.0):
    img = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(img)
    _row(d, 0, "Winlink: %s pend" % state.get("winlink_pending", "?"), WHITE, t)
    if state.get("mode") == "AP":
        wifi = "WiFi: %s dev" % state.get("wifi_clients", "?")
    else:
        wifi = "WiFi: client"
    _row(d, 1, wifi, WHITE, t)
    _row(d, 2, "BT/BLE: %s" % state.get("bt_clients", "?"), WHITE, t)
    d.line([0, 3 * ROW_H - 1, W - 1, 3 * ROW_H - 1], fill=BAR_BG)
    _traffic(d, state.get("frames", []), 3, 4, t)
    _status_bar(d, state)
    return img


# ---------------------------------------------------------------------------
# VARA screens (Xara running instead of Dire Wolf). Data: tft/xara_feed.py.
#
#   VARA monitor                          VARA log
#     VARA FM wide        [TX][RX][BSY]     VARA log            FM wide
#     Linked KP3M-12   (state, colored)     15:41 Linked KP3M>KP3M-12 wide
#     S/N 12.3 dB          speed lvl 7      15:43 Disconnected (top 18090 BPM)
#     2412 bps        18090 BPM             ...
#     RX ████████   TX ██                   Retries 2   to send 1.2 kB
#     waterfall (last ~5 s)                 AP 172.16.0.1
#     S/N graph (last 90 s)
#     AP 172.16.0.1

ORANGE = (0xFF, 0x9F, 0x1C)
VARA_KIND_COLOR = {"idle": GREY, "calling": YELLOW, "connected": RX_GREEN, "closing": ORANGE}


def _wf_palette():
    """black -> blue -> cyan -> yellow -> red -> white, 256 entries."""
    stops = [(0, (0, 0, 0)), (60, (0, 0, 140)), (120, (0, 170, 220)),
             (170, (250, 230, 40)), (220, (255, 60, 30)), (255, (255, 255, 255))]
    pal = []
    for i in range(256):
        for (a, ca), (b, cb) in zip(stops, stops[1:]):
            if a <= i <= b:
                f = (i - a) / float(b - a or 1)
                pal.extend(int(ca[k] + (cb[k] - ca[k]) * f) for k in range(3))
                break
    return pal


_WF_PAL = _wf_palette()


def _pill(d, x, y, text, on, color):
    f = font(12, True)
    w = int(_text_w(d, text, f)) + 8
    d.rounded_rectangle([x, y, x + w, y + 16], radius=4,
                        fill=color if on else BAR_BG, outline=None)
    d.text((x + 4, y + 1), text, font=f, fill=BLACK if on else GREY)
    return w


def _fit_row(d, x, y, text, color, size, bold=False, maxw=W - 4, t=0.0):
    f = font(size, bold)
    width = _text_w(d, text, f)
    if width <= maxw:
        d.text((x, y), text, font=f, fill=color)
        return
    span = width + SCROLL_GAP
    off = int((t * SCROLL_PX_PER_S) % span)
    d.text((x - off, y), text, font=f, fill=color)
    d.text((x - off + span, y), text, font=f, fill=color)


def _meter(d, x, y, w, h, dbfs, color, label):
    f = font(11, True)
    d.text((x, y - 1), label, font=f, fill=GREY)
    bx = x + 18
    d.rectangle([bx, y, bx + w, y + h], fill=BAR_BG)
    try:
        v = max(0.0, min(1.0, (float(dbfs) + 60.0) / 60.0))   # -60..0 dBFS
    except (TypeError, ValueError):
        v = 0.0
    if v > 0:
        d.rectangle([bx, y, bx + int(w * v), y + h], fill=color)


def _waterfall(img, rows, y, h, floor=-130.0, step=0.5):
    """Newest row at the bottom. Values are steps of `step` dB above `floor`;
    shown from -110 to -50 dB for contrast."""
    if not rows:
        return
    rows = rows[-h:]
    width = len(rows[0]) or 1
    lo = int((-110.0 - floor) / step)
    hi = int((-50.0 - floor) / step)
    scale = 255.0 / max(1, hi - lo)
    lut = bytes(max(0, min(255, int((v - lo) * scale))) for v in range(256))
    data = b"".join(r[:width].ljust(width, b"\0").translate(lut) for r in rows)
    pic = Image.frombytes("L", (width, len(rows)), data).convert("P")
    pic.putpalette(_WF_PAL)
    pic = pic.resize((W, len(rows)), Image.NEAREST).convert("RGB")
    img.paste(pic, (0, y + h - len(rows)))


def _sn_graph(d, hist, y, h):
    d.rectangle([0, y, W - 1, y + h], fill=(10, 14, 18))
    f = font(10)
    d.text((2, y), "S/N", font=f, fill=GREY)
    lo, hi = -10.0, 30.0
    for db, c in ((0, (40, 48, 56)), (10, (40, 48, 56)), (20, (40, 48, 56))):
        yy = y + h - int((db - lo) / (hi - lo) * h)
        d.line([22, yy, W - 1, yy], fill=c)
    n = len(hist)
    if not n:
        return
    x0 = W - n * (W - 24) / float(max(n, 90))
    prev = None
    for i, v in enumerate(hist):
        x = int(x0 + i * (W - 24) / float(max(n, 90)))
        if v is None:
            prev = None
            continue
        yy = y + h - int((max(lo, min(hi, v)) - lo) / (hi - lo) * h)
        if prev:
            d.line([prev, (x, yy)], fill=RX_GREEN, width=2)
        prev = (x, yy)


def vara_screen(state, t=0.0):
    """VARA monitor: state, S/N, speed, BPM, levels, waterfall, S/N graph."""
    from tft.xara_feed import link_label, bpm, TX_STATES
    v = state.get("vara") or {}
    s = v.get("status") or {}
    img = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W - 1, 25], fill=BAR_BG)
    mode = (v.get("mode") or "").strip()
    st = s.get("state", "")
    ptt = bool(s.get("ptt"))
    rx = st in ("Receiving", "Answering") or (not ptt and st not in ("", "Listen")
                                              and st not in TX_STATES)
    x = W - 3
    for text, on, color in (("BSY", s.get("busy"), YELLOW), ("RX", rx, RX_GREEN),
                            ("TX", ptt, TX_RED)):
        w = int(_text_w(d, text, font(12, True))) + 8
        x -= w
        _pill(d, x, 5, text, on, color)
        x -= 3
    title = ("VARA " + mode).strip()
    size = 17
    while size > 11 and _text_w(d, title, font(size, True)) > x - 6:
        size -= 1
    d.text((3, 2 + (17 - size) // 2), title, font=font(size, True), fill=WHITE)
    if not v.get("connected"):
        _fit_row(d, 4, 40, "Xara starting...", GREY, 22, True)
        _fit_row(d, 4, 76, "VARA monitor appears", GREY, 17)
        _fit_row(d, 4, 98, "when Xara is up", GREY, 17)
        _status_bar(d, state)
        return img
    label, kind = link_label(st, s.get("peer", ""))
    _fit_row(d, 4, 29, label, VARA_KIND_COLOR.get(kind, WHITE), 22, True, t=t)
    sn = s.get("sn")
    _fit_row(d, 4, 58, "S/N %s dB" % ("%.1f" % sn if sn is not None and kind == "connected"
                                       else "--"), WHITE, 18)
    lvl = s.get("level") or 0
    if kind == "connected" and lvl:
        f = font(16, True)
        txt = "SL %d" % lvl
        d.text((W - 4 - _text_w(d, txt, f), 60), txt, font=f, fill=YELLOW)
    br = s.get("bitrate") or {}
    bps = br.get("bps") or 0
    if kind == "connected" and bps:
        _fit_row(d, 4, 82, "%d bps" % bps, WHITE, 18)
        f = font(18, True)
        txt = "%d BPM" % bpm(bps)
        d.text((W - 4 - _text_w(d, txt, f), 82), txt, font=f, fill=RX_GREEN)
    else:
        _fit_row(d, 4, 82, "bps --    BPM --", GREY, 18)
    _meter(d, 2, 108, 96, 7, (s.get("rx") or {}).get("rms"), RX_GREEN, "RX")
    _meter(d, 122, 108, 96, 7, (s.get("tx") or {}).get("rms"), TX_RED, "TX")
    _waterfall(img, v.get("rows") or [], 120, 52, v.get("wf_floor", -130.0), v.get("wf_step", 0.5))
    _sn_graph(d, v.get("sn_hist") or [], 174, 34)
    _status_bar(d, state)
    return img


def vara_log(state, t=0.0):
    """Screen 2 with Xara: session log and counters."""
    v = state.get("vara") or {}
    s = v.get("status") or {}
    img = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W - 1, 25], fill=BAR_BG)
    _fit_row(d, 3, 2, "VARA log", WHITE, 17, True)
    mode = (v.get("mode") or "").strip()
    f = font(14)
    d.text((W - 4 - _text_w(d, mode, f), 5), mode, font=f, fill=GREY)
    lines = (v.get("log") or [])[-6:]
    if not lines:
        _fit_row(d, 4, 32, "no VARA sessions yet", GREY, 17)
    for i, (hhmm, text) in enumerate(lines):
        color = RX_GREEN if text.startswith("Linked") else (
            TX_RED if text.startswith(("Unable", "Link timed")) else WHITE)
        _fit_row(d, 4, 30 + i * 24, "%s %s" % (hhmm, text), color, 16, t=t)
    buf = s.get("buffer") or 0
    info = "Retries %s   to send %s" % (s.get("retries", 0),
                                         ("%.1f kB" % (buf / 1000.0)) if buf >= 1000 else "%d B" % buf)
    _fit_row(d, 4, 180, info, GREY, 15)
    _status_bar(d, state)
    return img
