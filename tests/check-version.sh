#!/usr/bin/env bash
# The version lives in three places; they must agree.
set -euo pipefail
cd "$(dirname "$0")/.."
m=$(sed -n 's/^VERSION=//p' kiwi.manifest)
p=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' lib/kiwiserver/__init__.py)
c=$(grep -m1 -oE '^## \[?[0-9]+\.[0-9]+\.[0-9]+' CHANGELOG.md | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
echo "manifest=$m package=$p changelog=$c"
[[ $m == "$p" && $m == "$c" ]] || { echo "version mismatch" >&2; exit 1; }
