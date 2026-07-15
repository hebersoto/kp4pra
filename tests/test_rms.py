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
