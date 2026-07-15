# Native Python Winlink RMS

KP4PRA TNC includes a native Python RMS gateway. It does not install, invoke,
or generate configuration for LinBPQ.

## Data path

`RF client <-> Dire Wolf AX.25/KISS <-> kp4pra-tnc-rms.service <-> Winlink CMS`

The service implements the minimum required gateway functions:

- KISS TCP framing to Dire Wolf
- one-hop AX.25 connected-mode session acceptance
- modulo-8 I-frame sequencing and RR acknowledgements
- Winlink CMS gateway authentication
- transparent B2F byte-stream forwarding between the RF client and CMS
- modulo-8 TX window flow control (at most 7 unacknowledged I-frames;
  peer N(R) values from I- and S-frames are processed as acknowledgements)
- one active RF/CMS session at a time

## Configuration

All persistent settings are in `/rw/kp4pra-tnc/config.yaml`:

```yaml
rms:
  enabled: true
  cms_call: "MYCALL-10"
  cms_password: "SECRET"
  frequency_hz: 145050000
  mode: "PACKET-1200"
```

The Dire Wolf host and port are reused from the existing `direwolf` section.
No second configuration file is generated.

## Scope

This first implementation intentionally supports direct, single-hop packet RMS
operation with one user session. Digipeater paths, multiple simultaneous users,
selective-repeat retransmission, local message storage, and hybrid forwarding
are outside the minimalist RMS scope.
