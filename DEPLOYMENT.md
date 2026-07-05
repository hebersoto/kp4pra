# KP4PRA TNC — Read-Only Production Deployment & SD Image Creation

The proven layout (running on the reference unit):
mmcblk0p1  /     ext4  ro       5G    OS + application (read-only in production)
mmcblk0p2  /rw   ext4  rw       64M+  config, BlueZ pairing state, direwolf.conf
tmpfs      /run                        runtime status (volatile)
journald   Storage=volatile            no logs on disk

## Part A — Partitioning (fresh card or existing system)

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

## Part C — Flip to read-only

```bash
sudo sed -i 's|\(\s/\s\+ext4\s\+\)defaults|\1defaults,ro|' /etc/fstab
grep ' / ' /etc/fstab      # verify: defaults,ro
sudo reboot
```

After reboot:
```bash
mount | grep ' / ' | grep ro && echo "root RO OK"
systemctl --failed         # must be empty
# Full functional pass: dashboard green, phone connects, config save works,
# pairing via wizard works (workflow remounts rw -> action -> ro -> reboot).
```
To temporarily undo for maintenance: `sudo mount -o remount,rw /`
(remount ro again or reboot when done). To undo permanently, remove `,ro`
from fstab.

## Part D — Creating the golden SD image

Do this from a SECOND Linux machine (or another SBC) with the source SD
card in a USB reader (shown as /dev/sdX — VERIFY with lsblk, wrong X
destroys a disk).

1. Prepare the source system (on the Pi, before shutdown):
```bash
# Generalize: remove secrets and identity that must not be cloned
sudo rm -f /home/kp4pra/.git-credentials /home/kp4pra/.bash_history
sudo rm -f /etc/ssh/ssh_host_*            # regenerated on first boot by most images;
                                          # if not: ssh-keygen -A in a firstboot unit
# Optional: clear WiFi credentials if the image will be distributed
# Optional: reset station config to defaults in /rw/kp4pra-tnc/config.yaml
# Remove pairing state so each unit pairs its own phones:
sudo rm -rf /rw/kp4pra-tnc/bluetooth/*
sudo poweroff
```

2. Capture only the used part of the card (p1 5G + p2 64M ≈ 5.1G, not 32G):
```bash
# End of p2 in sectors:
sudo fdisk -l /dev/sdX            # note End of sdX2, e.g. 10625023
# Image = (End+1) sectors:
sudo dd if=/dev/sdX of=kp4pra-tnc.img bs=512 count=10625024 status=progress
# Compress for storage/distribution:
xz -T0 -9 kp4pra-tnc.img          # -> kp4pra-tnc.img.xz (typically ~1-2 GB)
```

3. Flash to a new card (any size >= image size):
```bash
xz -dc kp4pra-tnc.img.xz | sudo dd of=/dev/sdY bs=4M status=progress conv=fsync
```

4. First boot on the new unit:
- Set hostname if desired (requires temporary rw remount).
- Open the web UI -> Config: set the unit's callsign/station info, Apply.
- Pair the phones for this unit via the wizards.
- Regenerate SSH host keys if step 1 removed them and the OS doesn't
  auto-regenerate: `sudo mount -o remount,rw / && sudo ssh-keygen -A && sudo mount -o remount,ro /`

## Alternative layout (designed, NOT yet implemented): zram /rw

A zram-backed /rw with write-through of BlueZ+config to a tiny /persist
partition was designed for zero SD writes even during BlueZ chatter.
Status: design only - the simple /rw-partition layout above is what has
been validated in operation. Revisit if SD wear from BlueZ becomes a
concern (in practice /rw is only written on pairing and config saves).
