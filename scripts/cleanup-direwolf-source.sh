#!/bin/bash
# KP4PRA TNC - remove the Dire Wolf SOURCE/BUILD tree after installation.
# The built binary lives in /usr/local/bin and is independent of the source,
# so the source (~180MB) is only needed to build/rebuild Dire Wolf.
# This script NEVER touches the installed binary or the running service.
set -euo pipefail

BIN="$(command -v direwolf || echo /usr/local/bin/direwolf)"

# Parse args: -y/--yes sets non-interactive; the first NON-flag arg (if any)
# is an explicit source path. Default source is $HOME/direwolf.
ASSUME_YES=0
SRC=""
for arg in "$@"; do
    case "$arg" in
        -y|--yes) ASSUME_YES=1 ;;
        -*)       ;;                       # ignore unknown flags
        *)        [ -z "$SRC" ] && SRC="$arg" ;;   # first non-flag = source
    esac
done
[ -z "$SRC" ] && SRC="$HOME/direwolf"
SRC="${SRC%/}"                             # strip any trailing slash

echo "KP4PRA TNC - Dire Wolf source cleanup"
echo

# Guard 1: the installed binary must exist and be executable.
if [ ! -x "$BIN" ]; then
    echo "ABORT: Dire Wolf binary not found at '$BIN'."
    exit 1
fi
echo "Installed binary: $BIN  (will NOT be touched)"

# Guard 2: the binary the service runs must NOT live inside the source tree.
# Check the ExecStart BINARY PATH (first token), not the whole command line -
# the -c config path (…/direwolf.conf) can contain the source path as a
# substring and must not trigger a false match.
if systemctl cat direwolf.service >/dev/null 2>&1; then
    exec_bin="$(systemctl cat direwolf.service \
                | sed -n 's/^ExecStart=\([^ ]*\).*/\1/p' | head -1)"
    real_exec="$(readlink -f "$exec_bin" 2>/dev/null || echo "$exec_bin")"
    real_src="$(readlink -f "$SRC" 2>/dev/null || echo "$SRC")"
    case "$real_exec" in
        "$real_src"/*)
            echo "ABORT: direwolf.service runs the binary from inside the source tree:"
            echo "  $exec_bin"
            echo "Install the binary to /usr/local/bin first, then re-run."
            exit 1
            ;;
    esac
    echo "Service runs the binary from: $exec_bin  (outside the source tree). Good."
fi

# Guard 3: the source dir must exist.
if [ ! -d "$SRC" ]; then
    echo "Nothing to do: source directory '$SRC' does not exist."
    exit 0
fi

size="$(du -sh "$SRC" 2>/dev/null | cut -f1)"
echo
echo "About to remove Dire Wolf source tree: $SRC  ($size)"
if [ "$ASSUME_YES" -eq 1 ]; then
    echo "Proceeding (--yes)."
else
    read -r -p "Proceed? [y/N] " ans
    case "$ans" in
        [yY]|[yY][eE][sS]) ;;
        *) echo "Cancelled. Nothing removed."; exit 0 ;;
    esac
fi

rm -rf "$SRC"
echo "Removed $SRC (reclaimed ~$size). Dire Wolf continues to run from $BIN."
