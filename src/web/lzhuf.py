"""
KP4PRA TNC - LZHUF codec for Winlink B2F (Phase 4, step 2).

Winlink compresses forwarded message bodies with LZHUF: LZSS dictionary
compression followed by adaptive (dynamic) Huffman coding. This is a
clean-room implementation of Haruhiko Okumura's LZHUF as used by
Winlink / F6FBB / pat, written from the algorithm.

Winlink framing:
  * The compressed stream is preceded by the ORIGINAL (uncompressed)
    length as a little-endian uint32.
  * A 1-byte checksum trails the logical content: the B2F protocol uses
    the two's-complement of the sum of the *uncompressed* bytes (mod 256)
    as the message checksum in the FS response. We expose that checksum
    separately (compute_checksum) since it is carried in the protocol,
    not embedded in the LZHUF stream itself.

Pure logic: no I/O. Correctness here is proven by compress->decompress
identity round-trips; byte-exact interoperability with CMS/RMS is
confirmed against the live target during dry-run/first-send (the true
oracle), per the Phase 4 plan.

Reference: Okumura LZHUF (public domain). Parameters below are the
standard LZHUF constants used by the Winlink/FBB variant.
"""

# ── LZSS parameters ──────────────────────────────────────────────────────────
N = 4096            # ring buffer size
F = 60              # upper limit for match length
THRESHOLD = 2       # encode string into position/length if length > THRESHOLD
NIL = N             # index for root of binary search trees

# ── Adaptive Huffman parameters ──────────────────────────────────────────────
N_CHAR = 256 - THRESHOLD + F      # kinds of characters (character code = 0..N_CHAR-1)
T = N_CHAR * 2 - 1                # size of table
R = T - 1                        # position of root
MAX_FREQ = 0x8000                # updates tree when the root frequency reaches this

# ── Position codec (canonical LZHUF fixed tables) ────────────────────────────
#
# The upper 6 bits of a 12-bit position are encoded with a fixed Huffman-like
# code (p_len/p_code below); the lower 6 bits are emitted raw. Decoding uses
# d_code (top 6-bit value for each 8-bit lookahead) and d_len (number of bits
# the code occupies).  These are Okumura's canonical LZHUF tables.

p_len = bytes([
    3, 4, 4, 4, 5, 5, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6,
    6, 6, 6, 6, 6, 6, 6, 6, 7, 7, 7, 7, 7, 7, 7, 7,
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
    8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8,
])

p_code = bytes([
    0x00, 0x20, 0x30, 0x40, 0x50, 0x58, 0x60, 0x68,
    0x70, 0x78, 0x80, 0x88, 0x90, 0x94, 0x98, 0x9C,
    0xA0, 0xA4, 0xA8, 0xAC, 0xB0, 0xB4, 0xB8, 0xBC,
    0xC0, 0xC2, 0xC4, 0xC6, 0xC8, 0xCA, 0xCC, 0xCE,
    0xD0, 0xD2, 0xD4, 0xD6, 0xD8, 0xDA, 0xDC, 0xDE,
    0xE0, 0xE2, 0xE4, 0xE6, 0xE8, 0xEA, 0xEC, 0xEE,
    0xF0, 0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7,
    0xF8, 0xF9, 0xFA, 0xFB, 0xFC, 0xFD, 0xFE, 0xFF,
])

d_code = bytes([
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01,
    0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01,
    0x02, 0x02, 0x02, 0x02, 0x02, 0x02, 0x02, 0x02,
    0x02, 0x02, 0x02, 0x02, 0x02, 0x02, 0x02, 0x02,
    0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03,
    0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03,
    0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04,
    0x05, 0x05, 0x05, 0x05, 0x05, 0x05, 0x05, 0x05,
    0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06,
    0x07, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07,
    0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08,
    0x09, 0x09, 0x09, 0x09, 0x09, 0x09, 0x09, 0x09,
    0x0A, 0x0A, 0x0A, 0x0A, 0x0A, 0x0A, 0x0A, 0x0A,
    0x0B, 0x0B, 0x0B, 0x0B, 0x0B, 0x0B, 0x0B, 0x0B,
    0x0C, 0x0C, 0x0C, 0x0C, 0x0D, 0x0D, 0x0D, 0x0D,
    0x0E, 0x0E, 0x0E, 0x0E, 0x0F, 0x0F, 0x0F, 0x0F,
    0x10, 0x10, 0x10, 0x10, 0x11, 0x11, 0x11, 0x11,
    0x12, 0x12, 0x12, 0x12, 0x13, 0x13, 0x13, 0x13,
    0x14, 0x14, 0x14, 0x14, 0x15, 0x15, 0x15, 0x15,
    0x16, 0x16, 0x16, 0x16, 0x17, 0x17, 0x17, 0x17,
    0x18, 0x18, 0x19, 0x19, 0x1A, 0x1A, 0x1B, 0x1B,
    0x1C, 0x1C, 0x1D, 0x1D, 0x1E, 0x1E, 0x1F, 0x1F,
    0x20, 0x20, 0x21, 0x21, 0x22, 0x22, 0x23, 0x23,
    0x24, 0x24, 0x25, 0x25, 0x26, 0x26, 0x27, 0x27,
    0x28, 0x28, 0x29, 0x29, 0x2A, 0x2A, 0x2B, 0x2B,
    0x2C, 0x2C, 0x2D, 0x2D, 0x2E, 0x2E, 0x2F, 0x2F,
    0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37,
    0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x3E, 0x3F,
])

d_len = bytes([
    0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03,
    0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03,
    0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03,
    0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03, 0x03,
    0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04,
    0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04,
    0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04,
    0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04,
    0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04,
    0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04,
    0x05, 0x05, 0x05, 0x05, 0x05, 0x05, 0x05, 0x05,
    0x05, 0x05, 0x05, 0x05, 0x05, 0x05, 0x05, 0x05,
    0x05, 0x05, 0x05, 0x05, 0x05, 0x05, 0x05, 0x05,
    0x05, 0x05, 0x05, 0x05, 0x05, 0x05, 0x05, 0x05,
    0x05, 0x05, 0x05, 0x05, 0x05, 0x05, 0x05, 0x05,
    0x05, 0x05, 0x05, 0x05, 0x05, 0x05, 0x05, 0x05,
    0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06,
    0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06,
    0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06,
    0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06,
    0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06,
    0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06,
    0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06,
    0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06, 0x06,
    0x07, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07,
    0x07, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07,
    0x07, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07,
    0x07, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07,
    0x07, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07,
    0x07, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07, 0x07,
    0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08,
    0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08, 0x08,
])


def _encode_position(bw, pos):
    # pos is a 12-bit distance. High 6 bits via p_code/p_len (variable
    # length, MSB-first, left-justified into 16 bits); low 6 bits raw.
    i = pos >> 6
    bw.put_code(p_len[i], (p_code[i] << 8) & 0xFFFF)
    bw.put_code(6, ((pos & 0x3F) << 10) & 0xFFFF)


def _decode_position(br):
    # Recover a 12-bit distance. Read an 8-bit lookahead; d_code maps it to
    # the high 6 bits and d_len gives how many of those 8 bits the prefix
    # used. The bits of `i` below the prefix are the top of the 6-bit low
    # field; read the remaining low bits from the stream.
    i = br.get_byte()
    c = d_code[i] << 6
    used = d_len[i]
    have = 8 - used
    low = i & ((1 << have) - 1)
    for _ in range(6 - have):
        low = (low << 1) | br.get_bit()
    return c | (low & 0x3F)


# ── Bit I/O ──────────────────────────────────────────────────────────────────

class _BitWriter:
    def __init__(self):
        self.out = bytearray()
        self.buf = 0
        self.mask = 0x80

    def put_bit(self, bit):
        if bit:
            self.buf |= self.mask
        self.mask >>= 1
        if self.mask == 0:
            self.out.append(self.buf)
            self.buf = 0
            self.mask = 0x80

    def put_code(self, length, code):
        # code is left-justified in a 16-bit field.
        while length > 0:
            length -= 1
            self.put_bit((code >> 15) & 1)
            code = (code << 1) & 0xFFFF

    def flush(self):
        if self.mask != 0x80:
            self.out.append(self.buf)
            self.buf = 0
            self.mask = 0x80
        return bytes(self.out)


class _BitReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.buf = 0
        self.mask = 0

    def get_bit(self):
        if self.mask == 0:
            if self.pos < len(self.data):
                self.buf = self.data[self.pos]
                self.pos += 1
            else:
                self.buf = 0
            self.mask = 0x80
        bit = 1 if (self.buf & self.mask) else 0
        self.mask >>= 1
        return bit

    def get_byte(self):
        val = 0
        for _ in range(8):
            val = (val << 1) | self.get_bit()
        return val & 0xFF


# ── Adaptive Huffman tree ────────────────────────────────────────────────────

class _Huff:
    def __init__(self):
        self.freq = [0] * (T + 1)
        self.prnt = [0] * (T + N_CHAR)
        self.son = [0] * T
        self._start()

    def _start(self):
        for i in range(N_CHAR):
            self.freq[i] = 1
            self.son[i] = i + T
            self.prnt[i + T] = i
        i = 0
        j = N_CHAR
        while j <= R:
            self.freq[j] = self.freq[i] + self.freq[i + 1]
            self.son[j] = i
            self.prnt[i] = self.prnt[i + 1] = j
            i += 2
            j += 1
        self.freq[T] = 0xFFFF
        self.prnt[R] = 0

    def _reconst(self):
        # Collect leaf nodes, halving frequencies (rounding up).
        j = 0
        for i in range(T):
            if self.son[i] >= T:
                self.freq[j] = (self.freq[i] + 1) // 2
                self.son[j] = self.son[i]
                j += 1
        # Rebuild internal nodes.
        i = 0
        j = N_CHAR
        while j < T:
            k = i + 1
            f = self.freq[j] = self.freq[i] + self.freq[k]
            k = j - 1
            while f < self.freq[k]:
                k -= 1
            k += 1
            # shift freq[k..j-1] to freq[k+1..j]
            for m in range(j, k, -1):
                self.freq[m] = self.freq[m - 1]
            self.freq[k] = f
            for m in range(j, k, -1):
                self.son[m] = self.son[m - 1]
            self.son[k] = i
            i += 2
            j += 1
        # Rebuild parent pointers.
        for i in range(T):
            k = self.son[i]
            if k >= T:
                self.prnt[k] = i
            else:
                self.prnt[k] = self.prnt[k + 1] = i

    def update(self, c):
        if self.freq[R] == MAX_FREQ:
            self._reconst()
        c = self.prnt[c + T]
        while True:
            self.freq[c] += 1
            k = self.freq[c]
            # If order is disturbed, exchange nodes.
            l = c + 1
            if l < len(self.freq) and k > self.freq[l]:
                while l + 1 <= T and k > self.freq[l + 1]:
                    l += 1
                l -= 1
                self.freq[c] = self.freq[l]
                self.freq[l] = k
                i = self.son[c]
                self.prnt[i] = l
                if i < T:
                    self.prnt[i + 1] = l
                j = self.son[l]
                self.son[l] = i
                self.prnt[j] = c
                if j < T:
                    self.prnt[j + 1] = c
                self.son[c] = j
                c = l
            c = self.prnt[c]
            if c == 0:
                break

    def encode_char(self, c, bw):
        code = 0
        length = 0
        k = self.prnt[c + T]
        # Travel from leaf to root, accumulating bits.
        bits = []
        while k != R:
            bits.append(k & 1)
            k = self.prnt[k]
        for b in reversed(bits):
            code = (code << 1) | b
            length += 1
        # Emit MSB-first, left-justified into 16 bits.
        bw.put_code(length, (code << (16 - length)) & 0xFFFF)
        self.update(c)

    def decode_char(self, br):
        c = self.son[R]
        while c < T:
            c = self.son[c + br.get_bit()]
        c -= T
        self.update(c)
        return c


# ── LZSS + Huffman encode / decode ───────────────────────────────────────────

def _lzss_encode(data: bytes) -> bytes:
    huff = _Huff()
    bw = _BitWriter()

    # text_buf holds the ring buffer plus F-1 lookahead bytes.
    text_buf = bytearray(N + F - 1)
    lson = [0] * (N + 1)
    rson = [0] * (N + 257)
    dad = [0] * (N + 1)

    match_position = 0
    match_length = 0

    def init_tree():
        for i in range(N + 1, N + 257):
            rson[i] = NIL
        for i in range(N):
            dad[i] = NIL

    def insert_node(r):
        nonlocal match_position, match_length
        cmp = 1
        p = N + 1 + text_buf[r]
        rson[r] = lson[r] = NIL
        match_length = 0
        while True:
            if cmp >= 0:
                if rson[p] != NIL:
                    p = rson[p]
                else:
                    rson[p] = r
                    dad[r] = p
                    return
            else:
                if lson[p] != NIL:
                    p = lson[p]
                else:
                    lson[p] = r
                    dad[r] = p
                    return
            i = 1
            while i < F:
                cmp = text_buf[r + i] - text_buf[p + i]
                if cmp != 0:
                    break
                i += 1
            if i > match_length:
                match_position = p
                match_length = i
                if match_length >= F:
                    break
        dad[r] = dad[p]
        lson[r] = lson[p]
        rson[r] = rson[p]
        dad[lson[p]] = r
        dad[rson[p]] = r
        if rson[dad[p]] == p:
            rson[dad[p]] = r
        else:
            lson[dad[p]] = r
        dad[p] = NIL

    def delete_node(p):
        if dad[p] == NIL:
            return
        if rson[p] == NIL:
            q = lson[p]
        elif lson[p] == NIL:
            q = rson[p]
        else:
            q = lson[p]
            if rson[q] != NIL:
                while rson[q] != NIL:
                    q = rson[q]
                rson[dad[q]] = lson[q]
                dad[lson[q]] = dad[q]
                lson[q] = lson[p]
                dad[lson[p]] = q
            rson[q] = rson[p]
            dad[rson[p]] = q
        dad[q] = dad[p]
        if rson[dad[p]] == p:
            rson[dad[p]] = q
        else:
            lson[dad[p]] = q
        dad[p] = NIL

    init_tree()
    s = 0
    r = N - F
    for i in range(r):
        text_buf[i] = 0x20

    src = bytes(data)
    src_pos = 0
    length = 0
    while length < F and src_pos < len(src):
        text_buf[r + length] = src[src_pos]
        src_pos += 1
        length += 1
    if length == 0:
        return bw.flush()

    for i in range(1, F + 1):
        insert_node(r - i)
    insert_node(r)

    while length > 0:
        if match_length > length:
            match_length = length
        if match_length <= THRESHOLD:
            match_length = 1
            huff.encode_char(text_buf[r], bw)
        else:
            huff.encode_char(255 - THRESHOLD + match_length, bw)
            _encode_position(bw, (r - match_position - 1) & (N - 1))
        last_match_length = match_length
        i = 0
        while i < last_match_length and src_pos < len(src):
            delete_node(s)
            c = src[src_pos]
            src_pos += 1
            text_buf[s] = c
            if s < F - 1:
                text_buf[s + N] = c
            s = (s + 1) & (N - 1)
            r = (r + 1) & (N - 1)
            insert_node(r)
            i += 1
        while i < last_match_length:
            i += 1
            delete_node(s)
            s = (s + 1) & (N - 1)
            r = (r + 1) & (N - 1)
            length -= 1
            if length:
                insert_node(r)

    return bw.flush()


def _lzss_decode(data: bytes, original_len: int) -> bytes:
    huff = _Huff()
    br = _BitReader(data)
    text = bytearray(N)
    for i in range(N - F):
        text[i] = 0x20
    r = N - F
    out = bytearray()
    count = 0
    while count < original_len:
        c = huff.decode_char(br)
        if c < 256:
            out.append(c)
            text[r] = c
            r = (r + 1) & (N - 1)
            count += 1
        else:
            pos = (r - _decode_position(br) - 1) & (N - 1)
            k = c - 255 + THRESHOLD
            for _ in range(k):
                b = text[pos]
                out.append(b)
                text[r] = b
                r = (r + 1) & (N - 1)
                pos = (pos + 1) & (N - 1)
                count += 1
                if count >= original_len:
                    break
    return bytes(out)


# ── Public API ───────────────────────────────────────────────────────────────

# ── Winlink B2 CRC-16 (XModem CRC-CCITT, poly 0x1021, init 0x0000) ────────────
# Matches the reference implementation (la5nta/wl2k-go lzhuf/crc.go), which the
# source notes is the same variant Airmail and Winlink 2000 use. The CRC is
# computed over the bytes PLUS two trailing zero bytes (crc() appends 0,0).

_CRC16_TAB = None


def _build_crc16_tab():
    tab = []
    for i in range(256):
        c = i << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if (c & 0x8000) else ((c << 1) & 0xFFFF)
        tab.append(c)
    return tab


def _crc16_update(cp, sm):
    return (((sm << 8) & 0xFF00) ^ _CRC16_TAB[(sm >> 8) & 0xFF] ^ cp) & 0xFFFF


def crc16(data: bytes) -> int:
    """Winlink B2 CRC-16 over data + two trailing zero bytes."""
    global _CRC16_TAB
    if _CRC16_TAB is None:
        _CRC16_TAB = _build_crc16_tab()
    sm = 0
    for c in data:
        sm = _crc16_update(c, sm)
    for c in (0, 0):
        sm = _crc16_update(c, sm)
    return sm


def compute_checksum(data: bytes) -> int:
    """Winlink B2F message checksum (per the FS/proposal accounting): two's
    complement of the sum of the (uncompressed) bytes, mod 256. Distinct
    from the CRC-16 that frames the compressed block."""
    return (-sum(data)) & 0xFF


def compress(data: bytes) -> bytes:
    """LZHUF-compress `data` into the Winlink B2 stream:
        [CRC16 (2 bytes, LE)] [fileSize (4 bytes, LE)] [compressed data]
    where CRC16 is over (fileSize_bytes + compressed_data). Matches the
    reference (la5nta/wl2k-go) byte layout."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("compress expects bytes")
    body = _lzss_encode(bytes(data))
    length_bytes = len(data).to_bytes(4, "little")
    sm = crc16(length_bytes + body)
    return sm.to_bytes(2, "little") + length_bytes + body


def decompress(blob: bytes) -> bytes:
    """Inverse of compress(): verify CRC-16, read the 4-byte length, decode."""
    if len(blob) < 6:
        raise ValueError("B2 stream too short")
    stored_crc = int.from_bytes(blob[:2], "little")
    length_bytes = blob[2:6]
    body = blob[6:]
    if crc16(length_bytes + body) != stored_crc:
        raise ValueError("B2 CRC-16 mismatch")
    original_len = int.from_bytes(length_bytes, "little")
    if original_len == 0:
        return b""
    return _lzss_decode(body, original_len)
