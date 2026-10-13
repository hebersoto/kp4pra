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
  5  current: FROM>TO (UI: FROM) 5  traffic
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
