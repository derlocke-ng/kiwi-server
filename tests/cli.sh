#!/usr/bin/env bash
# Smoke test of the installed-shape CLI: install into a temp prefix, run it
# from there, render the example fleet with throw-away secrets.
set -euo pipefail
cd "$(dirname "$0")/.."
repo=$PWD
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

export HOME="$tmp/home" KIWI_GUI=0
mkdir -p "$HOME/.ssh"
if command -v ssh-keygen >/dev/null; then
    ssh-keygen -q -t ed25519 -N '' -f "$HOME/.ssh/id_ed25519" >/dev/null
else
    echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEh+smoke+test+key+000000000000000000000000000 smoke" > "$HOME/.ssh/id_ed25519.pub"
fi
bash install.sh install
ks="$HOME/.local/bin/kiwi-server"

"$ks" version | grep -q "^kiwi-server "
"$ks" roles | grep -q node-cloud
"$ks" targets | grep -q debian

mkdir -p "$tmp/fleet/secrets"
cd "$tmp/fleet"
"$ks" init fleet.yaml
printf '[Interface]\nPrivateKey=x\n' > secrets/sh3.kiwi.conf
cp secrets/sh3.kiwi.conf secrets/sh4.kiwi.conf
"$ks" validate fleet.yaml
"$ks" render fleet.yaml --toolchain native
"$ks" list fleet.yaml | grep -q '^sh3 '
"$ks" list fleet.yaml --porcelain | python3 -c 'import json,sys; d=json.load(sys.stdin); assert len(d["hosts"])==4; assert all(not h["errors"] for h in d["hosts"])'
"$ks" script fleet.yaml gate | bash -n
test -f output/gate/gate.preseed.cfg
test -f output/sh3/sh3.bu
if command -v shellcheck >/dev/null; then shellcheck -S warning output/*/*.role.sh; fi
if command -v butane >/dev/null; then butane --strict output/sh3/sh3.bu >/dev/null; fi
# build must refuse politely without the tools, never half-build
if ! "$ks" build fleet.yaml lab1 --toolchain native 2>"$tmp/err"; then
    grep -qiE 'coreos-installer|not installed|missing' "$tmp/err"
fi
bash "$repo/install.sh" uninstall --purge >/dev/null
test ! -e "$ks"
echo "cli smoke test: ok"
