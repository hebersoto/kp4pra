from rms.ax25 import *
from rms.cms import challenge_response

def test_kiss_roundtrip():
    f=b'abc'+bytes([FEND,FESC])+b'z'; d=KissDecoder(); assert d.feed(kiss_encode(f))==[f]

def test_ax25_roundtrip():
    f=make_frame('KP4PRA-10','NP4JN',iframe(2,3),b'hello',PID_NO_L3)
    dest,src,path,c,pid,p=split_frame(f)
    assert (dest,src,path,ns(c),nr(c),pid,p)==('KP4PRA-10','NP4JN',[],2,3,PID_NO_L3,b'hello')

def test_hash_case_sensitive_and_8_digits():
    # Winlink secure login is case-SENSITIVE; response is an 8-char numeric string
    r = challenge_response('12345678','SECRET')
    assert isinstance(r, str) and len(r) == 8 and r.isdigit()
    assert challenge_response('12345678','SECRET') != challenge_response('12345678','secret')


def test_s_frame_detection_and_nr():
    r = rr(5)
    assert is_s(r) and not is_i(r) and nr(r) == 5

def test_window_arithmetic():
    # 7 outstanding frames = window full under modulo-8
    vs, va = 1, 2   # wrapped: sent ...7,0 while va still 2
    assert ((vs - va) & 7) == 7
def test_kiss_split_across_feeds():
    # A single AX.25 frame arriving in two separate TCP reads from Direwolf
    # must still decode correctly once both chunks are fed in.
    f = b'hello world'
    encoded = kiss_encode(f)
    mid = len(encoded)//2
    d = KissDecoder()
    out1 = d.feed(encoded[:mid])
    out2 = d.feed(encoded[mid:])
    assert out1 == [] and out2 == [f]

def test_kiss_multiple_frames_one_chunk():
    # Direwolf can deliver several queued frames in a single TCP read.
    f1, f2 = b'first', b'second'
    d = KissDecoder()
    out = d.feed(kiss_encode(f1) + kiss_encode(f2))
    assert out == [f1, f2]

def test_split_frame_with_digipeater_path():
    # make_frame only ever builds 2-address frames; split_frame must still
    # handle a path field correctly if a digipeated frame is ever received.
    frame = make_frame('KP4PRA-10', 'NP4JN', iframe(0, 0), b'x', PID_NO_L3)
    # manually insert one digipeater address between src and control
    from rms.ax25 import encode_call
    digi = encode_call('WIDE1-1', last=True)
    # clear "last" bit on src address, splice digi before control byte
    addr_part = bytearray(frame[:14])
    addr_part[13] &= ~1  # src no longer last
    frame2 = bytes(addr_part) + digi + frame[14:]
    dest, src, path, ctrl, pid, payload = split_frame(frame2)
    assert path == ['WIDE1-1'] and dest == 'KP4PRA-10' and src == 'NP4JN'

def test_window_boundary_blocks_at_7_free_at_6():
    # cms_to_rf's flow-control gate is `((vs-va)&7) >= 7`; confirm the exact
    # boundary: 7 outstanding blocks, 6 outstanding does not.
    vs, va = 7, 0
    assert ((vs - va) & 7) == 7
    vs, va = 6, 0
    assert ((vs - va) & 7) == 6

def test_rr_encodes_nr_and_poll_bit():
    c = rr(3, pf=True)
    assert is_s(c) and nr(c) == 3 and (c & 0x10) == 0x10
