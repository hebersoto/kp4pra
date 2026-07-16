# KP4PRA TNC — Read-Only Production Deployment & SD Image Creation


> **Architecture note:** persistent configuration lives on the /rw
> partition, NOT in /etc. Config saves and direwolf.conf Apply work with
> root read-only, no remount, no reboot. Only Bluetooth pairing uses the
> remount workflow (automated by the web UI). Guides that store config in
> /etc and remount rw for every change describe a different (simpler but
> less capable) model - do not mix the two.

The proven layout (running on the reference unit):
mmcblk0p1  /     ext4  ro       5G    OS + application (read-only in production)
mmcblk0p2  /rw   ext4  rw       64M+  config, BlueZ pairing state, direwolf.conf
tmpfs      /run                        runtime status (volatile)
journald   Storage=volatile            no logs on disk

## Part A — Partitioning (fresh card or existing system)

### Raspberry Pi OS — READ THIS FIRST (Part A differs from Armbian)

> **DANGER:** The fdisk/mkfs steps in this Part are written for the
> Armbian / Orange Pi layout where **p1 is the root filesystem**. On
> **Raspberry Pi OS the layout is inverted** - p1 is `/boot/firmware`
> and **p2 is the root filesystem**, and p2 **auto-expands to fill the
> entire card on first boot**. Running `mkfs.ext4 /dev/mmcblk0p2` on a
> Raspberry Pi **FORMATS YOUR ROOT FILESYSTEM and destroys the system.**
> Do NOT follow the Armbian steps below on a Raspberry Pi.

**On Raspberry Pi OS, choose ONE of these instead:**

**(a) `/rw` as a directory on root (simplest, fully supported):**

```bash
sudo mkdir -p /rw
```

The board runs read-WRITE root. It is still SD-friendly via tmpfs for
volatile directories (Part B2). Read-only-root mode (Part C) is not used
with this option. This is a perfectly fine production posture for most
users and is what `scripts/install.sh` expects by default.

**(b) Real `/rw` partition (only needed for read-only-root, Part C):**

The root partition fills the card, so you must shrink it and add a third
partition **offline from another Linux PC** - you cannot shrink a mounted
root filesystem. With the card in a USB reader on another machine
(card = `/dev/sdX`, verify with `lsblk` first), the easiest route is
GParted: shrink `/dev/sdX2` by ~512MB, then create an ext4 partition
`/dev/sdX3` labeled `kp4pra-rw` in the freed space. Reinsert the card in
the Pi, then:

```bash
sudo mkdir -p /rw
echo 'LABEL=kp4pra-rw  /rw  ext4  defaults,rw,noatime  0  2' | sudo tee -a /etc/fstab
sudo mount /rw
```

The Armbian partitioning steps that follow apply ONLY to Orange Pi /
Armbian images, where p1 is root and free space follows it.

---

## Part A (Armbian / Orange Pi) — Partitioning (fresh card or existing system)

The OS image occupies p1. Create p2 in the free space after it:

```bash
sudo fdisk -l /dev/mmcblk0        # note the END sector of p1
sudo fdisk /dev/mmcblk0
#  n, p, 2, <END_of_p1 + 1>, +64M, w
#  (fdisk may propose a wrong default start INSIDE p1 - always type the
#   start sector explicitly: p1's End + 1)
sudo mkfs.ext4 -L kp4pra-rw /dev/mmcblk0p2
sudo mkdir -p /rw
echo '/dev/mmcblk0p2  /rw  ext4  defaults,rw,noatime  0  2' | sudo tee -a /etc/fstab
sudo mount /rw
```

Then run scripts/install.sh and scripts/install-direwolf-integration.sh
(they populate /rw/kp4pra-tnc/ and set up the BlueZ bind mount).

## Part B — Pre-flight checklist BEFORE making root read-only

Every item must pass, or the system will misbehave (or not boot) with ro root:

```bash
# 1. /rw is a real partition, mounted, writable
mountpoint /rw && touch /rw/.t && rm /rw/.t && echo OK

# 2. BlueZ bind mount active and pointing at /rw
mountpoint /var/lib/bluetooth && findmnt /var/lib/bluetooth | grep /rw && echo OK

# 3. All persistent app state on /rw
ls -la /rw/kp4pra-tnc/config.yaml /rw/kp4pra-tnc/direwolf.conf
readlink /home/kp4pra/direwolf.conf   # must point into /rw

# 4. journald volatile (nothing will try to write /var/log/journal)
grep -r Storage /etc/systemd/journald.conf.d/ | grep -i volatile && echo OK

# 5. Runtime dir handled by tmpfiles (tmpfs)
cat /etc/tmpfiles.d/kp4pra-tnc.conf

# 6. Nothing else writes the root fs. Watch for writers during normal
#    operation + one pairing + one config save:
#    (fatrace: apt install fatrace)
sudo fatrace -f W | grep -vE "/rw/|/run/|/dev/|/proc/|/tmp/" &
#    ... exercise the system for a few minutes, then check output.
#    Common offenders to fix: apt timers (systemctl disable apt-daily{,-upgrade}.timer),
#    fake-hwclock, man-db.timer, dpkg triggers.

# 7. Swap: zram only (no swapfile on root)
cat /proc/swaps    # should show /dev/zram0 only
```


## Part B2 — tmpfs for stray writers (/var/tmp, /var/log)

journald volatile covers the journal, but other software (dpkg, cron,
third-party tools) writes /var/tmp and /var/log directly. Put these in RAM
so nothing stray reaches the SD card.

> **Raspberry Pi OS:** `/tmp` is ALREADY tmpfs by default - confirm with
> `findmnt /tmp` and do NOT add a `/tmp` line (it conflicts). Add only
> `/var/tmp` and `/var/log`. Mounting tmpfs over `/var/log` hides existing
> logs while mounted (services re-create what they need) - expected.

```bash
sudo tee -a /etc/fstab << 'FSTAB'
tmpfs /var/tmp tmpfs defaults,noatime,nosuid,nodev,mode=1777,size=32M 0 0
tmpfs /var/log tmpfs defaults,noatime,nosuid,nodev,mode=0755,size=32M 0 0
FSTAB
sudo systemctl daemon-reload
sudo mount /var/tmp && sudo mount /var/log
```

The `size=` values cap RAM use, not disk - they are independent of SD-card
size. 32M suits 512MB-RAM boards (Pi 3 A+, Zero 2 W); raise them on
boards with more RAM if logs are heavy.

Reduce writers at the source too:

```bash
printf 'Dir::Cache "";\nDir::Cache::archives "";\n' | sudo tee /etc/apt/apt.conf.d/99no-cache
sudo systemctl disable --now apt-daily.timer apt-daily-upgrade.timer man-db.timer 2>/dev/null
systemctl status rsyslog --no-pager 2>/dev/null && sudo systemctl disable --now rsyslog
```

## Part B3 — Operator convenience commands (rw / ro)

For manual maintenance at the shell (the web UI uses its own allowlisted
helpers and does not need these):

```bash
printf '#!/bin/bash\nmount -o remount,rw /\necho "Root is now read/write."\n' | sudo tee /usr/local/sbin/rw
printf '#!/bin/bash\nsync\nmount -o remount,ro /\necho "Root is now read-only."\n' | sudo tee /usr/local/sbin/ro
sudo chmod +x /usr/local/sbin/rw /usr/local/sbin/ro
```

Usage: `sudo rw` → make changes → `sudo ro` (or reboot).

## Part C — Flip to read-only

Only after every Part B / B2 / B3 check passes AND `/rw` is a real
partition (not the directory-on-root option, which keeps root read-write).

**Raspberry Pi OS** uses PARTUUID and `defaults,noatime` on the root line,
so edit it by hand (the Armbian sed below does not match):

```bash
sudo nano /etc/fstab
# on the "/" line change:  defaults,noatime
#                    to:    defaults,noatime,ro
grep ' / ' /etc/fstab       # verify ,ro present
sudo reboot
```

**Armbian / Orange Pi** (root line is plain `defaults`):

```bash
sudo sed -i 's|\(\s/\s\+ext4\s\+\)defaults|\1defaults,ro|' /etc/fstab
grep ' / ' /etc/fstab
sudo reboot
```

After reboot (either OS):

```bash
mount | grep ' / ' | grep ro && echo "root RO OK"
systemctl --failed          # must be empty
# Full functional pass: dashboard green, phone connects, config save works,
# pairing via wizard works (workflow remounts rw -> action -> ro -> reboot).
```

To temporarily undo for maintenance: `sudo mount -o remount,rw /` (remount
ro or reboot when done). To undo permanently, remove `,ro` from fstab.

## Part D — Creating the golden SD image

Do this from a SECOND Linux machine with the source SD card in a USB
reader (shown as /dev/sdX — VERIFY with lsblk, wrong X destroys a disk).

1. Prepare the source system (on the Pi, before shutdown):

```bash
sudo rm -f /home/kp4pra/.git-credentials /home/kp4pra/.bash_history
sudo rm -f /etc/ssh/ssh_host_*            # regenerated on first boot by most images
sudo rm -rf /rw/kp4pra-tnc/bluetooth/*    # each unit pairs its own phones
# Optional: clear WiFi client creds and reset station config for distribution
sudo poweroff
```

2. Capture only the used portion of the card — read the actual end sector,
   never assume a size (works on any card, any OS layout):

```bash
# Find the LAST used sector across all partitions (the highest End value):
sudo fdisk -l /dev/sdX
# Image size = (highest End sector + 1). Substitute it below as END:
sudo dd if=/dev/sdX of=kp4pra-tnc.img bs=512 count=$((END + 1)) status=progress
xz -T0 -9 kp4pra-tnc.img                  # -> kp4pra-tnc.img.xz
```

3. Flash to a new card (any size >= image size):

```bash
xz -dc kp4pra-tnc.img.xz | sudo dd of=/dev/sdY bs=4M status=progress conv=fsync
```

4. First boot on the new unit:
- Set hostname if desired (temporary rw remount if root is ro).
- Web UI → Config: set the unit's callsign/station info, Apply.
- Pair phones for this unit via the wizards.
- If step 1 removed SSH host keys and the OS did not auto-regenerate:
  `sudo ssh-keygen -A` (rw remount first if root is read-only).

## Part E — Recovery if the board does not boot after the ro flip

Remove the SD card, insert it into another Linux computer:

```bash
lsblk                                  # identify the card, e.g. /dev/sdX
sudo mkdir -p /mnt/sd
# Root is p2 on Raspberry Pi OS (p1 = /boot/firmware); p1 on Armbian:
sudo mount /dev/sdX2 /mnt/sd           # Raspberry Pi OS
# sudo mount /dev/sdX1 /mnt/sd         # Armbian / Orange Pi
sudo nano /mnt/sd/etc/fstab            # remove ",ro" from the / line;
                                       # optionally comment the tmpfs lines
sudo umount /mnt/sd && sync
```

Reinsert and boot; fix the underlying issue (check `systemctl --failed`
and the Part B checklist) before flipping back to ro.

## Alternative layout (designed, NOT yet implemented): zram /rw

A zram-backed /rw with write-through of BlueZ+config to a tiny /persist
partition was designed for zero SD writes even during BlueZ chatter.
Status: design only - the simple /rw-partition layout above is what has
been validated in operation. Revisit if SD wear from BlueZ becomes a
concern (in practice /rw is only written on pairing and config saves).
