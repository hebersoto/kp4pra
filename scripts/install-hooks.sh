#!/bin/bash
# Install KP4PRA TNC git hooks into this clone. Run once per board:
#   bash scripts/install-hooks.sh
set -e
root="$(git rev-parse --show-toplevel)"
install -m 755 "$root/scripts/git-hooks/pre-commit" "$root/.git/hooks/pre-commit"
echo "Installed pre-commit secret guard into .git/hooks/"
echo "Optional literals file (untracked): $root/.git/secret-guard-literals"
