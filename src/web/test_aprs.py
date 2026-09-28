"""Unit tests for aprs.py -- APRS messaging format, acks, passcode, TNC2."""
import aprs


def t(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    assert cond, name


# ---- addressee / text formatting ----
t("addressee padded to 9", aprs.format_addressee("KP3M") == "KP3M     ")
t("addressee with ssid", aprs.format_addressee("KP4DOG-10") == "KP4DOG-10")
t("addressee truncated to 9", len(aprs.format_addressee("VERYLONGCALL-15")) == 9)

t("text sanitize strips forbidden", aprs.sanitize_text("hi|there~{x") == "hitherex")
t("text clamps to 67", len(aprs.sanitize_text("A" * 200)) == 67)
t("text drops newlines", aprs.sanitize_text("a\r\nb") == "a b")

# ---- message field build ----
t("message no msgno", aprs.format_message("WU2Z", "Hello") == ":WU2Z     :Hello")
t("message with msgno", aprs.format_message("WU2Z", "Hello", 3) == ":WU2Z     :Hello{3")
t("message ssid addressee",
  aprs.format_message("KP4DOG-10", "test", "12") == ":KP4DOG-10:test{12")

# ---- ack / reject build ----
t("ack build", aprs.format_ack("N0CALL", 7) == ":N0CALL   :ack7")
t("rej build", aprs.format_reject("N0CALL", 7) == ":N0CALL   :rej7")

# ---- parse round-trips ----
def rt(addressee, text, msgno):
    field = aprs.format_message(addressee, text, msgno)
    p = aprs.parse_message(field)
    return p

p = rt("KP3M", "All OK 73", "42")
t("parse kind message", p["kind"] == "message")
t("parse addressee", p["addressee"] == "KP3M")
t("parse text", p["text"] == "All OK 73")
t("parse msgno", p["msgno"] == "42")

p = aprs.parse_message(":KP3M     :no ack here")
t("parse no-msgno message", p["kind"] == "message" and p["msgno"] is None
  and p["text"] == "no ack here")

# ack parse
p = aprs.parse_message(":KP3M     :ack42")
t("parse ack kind", p["kind"] == "ack")
t("parse ack addressee", p["addressee"] == "KP3M")
t("parse ack msgno", p["msgno"] == "42")

# reject parse
p = aprs.parse_message(":KP3M     :rej42")
t("parse reject", p["kind"] == "reject" and p["msgno"] == "42")

# non-message
t("parse non-message returns None kind",
  aprs.parse_message("!4903.50N/07201.75W-Test")["kind"] is None)
t("parse empty", aprs.parse_message("")["kind"] is None)

# a literal { in text that is NOT a valid msgno stays as text
p = aprs.parse_message(":KP3M     :math {set} notation")
t("literal brace kept as text",
  p["kind"] == "message" and p["msgno"] is None and "{set}" in p["text"])

# message text containing a brace-like tail that IS a valid msgno
p = aprs.parse_message(":KP3M     :hello world{abc12")
t("valid trailing msgno parsed",
  p["kind"] == "message" and p["msgno"] == "abc12" and p["text"] == "hello world")

# ---- msgno generator ----
g = aprs.MsgNoGen()
first = g.next(); second = g.next()
t("msgno increments", first == "1" and second == "2")
g2 = aprs.MsgNoGen(99999)
a = g2.next(); b = g2.next()
t("msgno wraps", a == "99999" and b == "1")

# ---- APRS-IS passcode ----
# Standard algorithm self-consistency + a couple of structural checks.
t("passcode is 15-bit", 0 <= aprs.passcode("KP3M") <= 0x7FFF)
t("passcode ignores ssid",
  aprs.passcode("KP3M-7") == aprs.passcode("KP3M"))
t("passcode case-insensitive",
  aprs.passcode("kp3m") == aprs.passcode("KP3M"))
# Known reference vector: the widely-published example call "N0CALL" -> 13023.
t("passcode N0CALL == 13023", aprs.passcode("N0CALL") == 13023)

login = aprs.aprsis_login("KP3M", "KP4PRA", "1.4.7")
t("login line format",
  login == "user KP3M pass %d vers KP4PRA 1.4.7" % aprs.passcode("KP3M"))
t("login with filter",
  aprs.aprsis_login("KP3M", filt="g/KP3M").endswith("filter g/KP3M"))

# ---- TNC2 build / parse ----
line = aprs.tnc2_line("KP3M", "APKP41", aprs.format_message("KP4DOG", "hi", "5"),
                      path=["TCPIP*"])
t("tnc2 build", line == "KP3M>APKP41,TCPIP*::KP4DOG   :hi{5")

d = aprs.parse_tnc2("KP4DOG-10>APKP41,WIDE1-1,WIDE2-1::KP3M     :Hello back{9")
t("tnc2 parse source", d["source"] == "KP4DOG-10")
t("tnc2 parse dest", d["dest"] == "APKP41")
t("tnc2 parse path", d["path"] == ["WIDE1-1", "WIDE2-1"])
t("tnc2 parse info is message",
  aprs.parse_message(d["info"])["kind"] == "message")
t("tnc2 comment ignored", aprs.parse_tnc2("# aprsc 2.1.0") is None)

# ---- full RX scenario: incoming message -> we build the ack ----
d = aprs.parse_tnc2("KP4DOG-10>APKP41,TCPIP*::KP3M     :Need status{17")
info = aprs.parse_message(d["info"])
t("incoming addressed to us", info["addressee"] == "KP3M" and info["kind"] == "message")
ack = aprs.format_ack(d["source"], info["msgno"])   # ack goes back to the sender
t("ack targets original sender", ack == ":KP4DOG-10:ack17")

print("\nALL APRS FORMAT TESTS PASSED")

# ---- position / Maidenhead ----
ll = aprs.maidenhead_to_latlon("FN31pr")
t("grid FN31pr lat ~41.73", abs(ll[0] - 41.729) < 0.01)
t("grid FN31pr lon ~-72.71", abs(ll[1] - (-72.708)) < 0.01)
t("grid 4-char works", aprs.maidenhead_to_latlon("FK68") is not None)
t("grid bad returns None", aprs.maidenhead_to_latlon("ZZ") is None)
t("format_lat", aprs.format_lat(41.72917) == "4143.75N")
t("format_lon west", aprs.format_lon(-72.708) == "07242.48W")
pos = aprs.position_report(41.72917, -72.708, "/", "-", "KP4PRA APRS")
t("position report format", pos == "=4143.75N/07242.48W-KP4PRA APRS")
t("position report messaging flag", pos[0] == "=")
t("position report ! variant", aprs.position_report(0,0,messaging=False)[0] == "!")
print("position report:", pos)
print("ALL APRS FORMAT TESTS PASSED (with position)")
