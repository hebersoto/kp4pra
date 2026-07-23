# Phase 4 — steps 1 & 2 (message builder + LZHUF codec)

Two library modules. **No routes, no behavior change** to the running
app — they are inert until step 3 (the dry-run B2F sender) wires them in.
Nothing here transmits or touches the queue.

Contents:
- `mailbuilder.py`  — queue record -> Winlink message (From: <station>@winlink.org,
  user Reply-To, <=78 wrap) + B2F proposal line.
- `lzhuf.py`        — clean-room LZHUF compressor/decompressor + checksum.
- `test_phase4_step12.py` — self-test proving both modules on your Python.

## Install (on a Phase 4 branch off dev)

```bash
cd ~/kp4pra-tnc
git checkout dev && git pull --ff-only
git checkout -b feature/webmail-phase4-delivery

# copy the two modules + the test into the repo
cp /path/to/mailbuilder.py        src/web/mailbuilder.py
cp /path/to/lzhuf.py              src/web/lzhuf.py
cp /path/to/test_phase4_step12.py src/web/test_phase4_step12.py
```

## Verify (must pass before we proceed to step 3)

```bash
cd ~/kp4pra-tnc
python3 -m py_compile src/web/mailbuilder.py src/web/lzhuf.py && echo "COMPILE OK"
PYTHONPATH=src/web python3 src/web/test_phase4_step12.py
```

Expect every line to print `OK` and finally
`ALL PHASE 4 STEP 1+2 CHECKS PASSED`. If anything says FAIL, the transfer
was corrupted — re-copy the file and re-run; do not proceed.

## Note on interoperability

The LZHUF codec round-trips perfectly against itself (proven by the
tests). Byte-exact interoperability with the live CMS/RMS is confirmed
during the step-3 dry-run against your safe target — that is the real
oracle. If you can produce a known-good `(plaintext, compressed)` sample
from pat or Winlink Express, share it and it can be verified in advance.

## Do NOT deploy to /opt yet

There is no reason to `cp` these into `/opt/kp4pra-tnc` or restart the
service — they are not imported by the app until step 3. Keep them on the
branch. No tag yet; we tag 1.4.4 after the dry-run is validated.
