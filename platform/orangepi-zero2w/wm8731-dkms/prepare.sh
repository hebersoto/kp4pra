#!/bin/bash
# KP4PRA TNC - DKMS PRE_BUILD for kp4pra-wm8731.
#
# Usage (run by DKMS in the build directory): prepare.sh <kernelver>
#
# 1. Work out the upstream kernel tag from <kernelver>
#    (6.18.44-current-sunxi64 -> v6.18.44, 6.19.0-edge-sunxi64 -> v6.19).
# 2. Get wm8731.c / wm8731.h / wm8731-i2c.c for exactly that tag:
#    cache (/var/cache/kp4pra-wm8731/<tag>) -> git.kernel.org stable ->
#    GitHub mirror. A successful download refreshes the cache, so later
#    rebuilds for the same kernel work offline.
# 3. Apply the KP4PRA patches (kp4pra_wm8731_patch.py). Any mismatch fails
#    the build loudly instead of producing a half-patched driver.
set -euo pipefail

KVER="${1:?usage: prepare.sh <kernelver>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE_ROOT="${KP4PRA_WM8731_CACHE:-/var/cache/kp4pra-wm8731}"
FILES="wm8731.c wm8731.h wm8731-i2c.c"

base="${KVER%%-*}"                       # 6.18.44
if [[ ! "$base" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]]; then
    echo "kp4pra-wm8731: cannot parse kernel version '$KVER'" >&2; exit 1
fi
IFS=. read -r maj min sub <<<"$base"
if [ -z "${sub:-}" ] || [ "$sub" = 0 ]; then TAG="v${maj}.${min}"; else TAG="v${maj}.${min}.${sub}"; fi
CACHE="$CACHE_ROOT/$TAG"
echo "kp4pra-wm8731: kernel $KVER -> upstream $TAG"

valid() {  # a real driver file, not an HTML error page
    [ -s "$1" ] && grep -q "WM8731" "$1" && ! grep -qi "<html" "$1"
}

fetch_from() {  # $1 = base URL builder name
    local f url tmp="$HERE/.dl"
    rm -rf "$tmp"; mkdir -p "$tmp"
    for f in $FILES; do
        case "$1" in
            kernelorg) url="https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/plain/sound/soc/codecs/$f?h=$TAG" ;;
            github)    url="https://raw.githubusercontent.com/gregkh/linux/$TAG/sound/soc/codecs/$f" ;;
        esac
        curl -fsSL --retry 2 --connect-timeout 15 --max-time 60 -o "$tmp/$f" "$url" || return 1
        valid "$tmp/$f" || return 1
    done
    mv "$tmp"/* "$HERE"/; rmdir "$tmp"
    return 0
}

have_all() { local f; for f in $FILES; do valid "$1/$f" || return 1; done; }

got=""
for src in kernelorg github; do
    if fetch_from "$src"; then got="$src"; break; fi
    echo "kp4pra-wm8731: download from $src failed"
done
rm -rf "$HERE/.dl"
if [ -n "$got" ]; then
    echo "kp4pra-wm8731: downloaded $TAG sources ($got)"
    if mkdir -p "$CACHE" 2>/dev/null; then
        for f in $FILES; do cp "$HERE/$f" "$CACHE/$f" 2>/dev/null || true; done
    fi
elif have_all "$CACHE"; then
    echo "kp4pra-wm8731: offline - using cached $TAG sources from $CACHE"
    for f in $FILES; do cp "$CACHE/$f" "$HERE/$f"; done
else
    echo "kp4pra-wm8731: ERROR: no internet and no cached sources for $TAG." >&2
    echo "  Connect the board to the internet and run:" >&2
    echo "    sudo dkms autoinstall -k $KVER" >&2
    exit 1
fi

python3 "$HERE/kp4pra_wm8731_patch.py" "$HERE"
