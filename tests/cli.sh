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
printf '[Interface]\nPrivateKey = x\nAddress = 10.8.0.25/24\n' > secrets/sh3.kiwi.conf
printf '[Interface]\nPrivateKey = x\nAddress = 10.8.0.6/24\n' > secrets/m1.kiwi.conf
"$ks" validate fleet.yaml
"$ks" render fleet.yaml --toolchain native
"$ks" list fleet.yaml | grep -q '^sh3 '
"$ks" list fleet.yaml --porcelain | python3 -c 'import json,sys; d=json.load(sys.stdin); assert len(d["hosts"])==4; assert all(not h["errors"] for h in d["hosts"])'
"$ks" script fleet.yaml gate | bash -n
test -f output/gate/gate.preseed.cfg
test -f output/gate/gate.stack/km-vpn-server/start.sh
test -f output/sh3/sh3.bu
test -f output/sh3/sh3.stack/docker-compose.yml
test -f output/m1/m1.stack/kiwi/gw.sh
test -f secrets/ca/kiwiCA.pem
grep -q 'KS_FILES\[ca_cert\]' output/lab1/lab1.role.sh
grep -q "KS_ROLE_VPN_IP='10.8.0.6'" output/m1/m1.role.sh        # from the WireGuard config's Address
grep -q 'FTLCONF_dns_hosts=.*10.8.0.25 cloud.sh3.kiwi' output/m1/m1.stack/docker-compose.yml
grep -q 'set $backend http://nextcloud-aio-apache:11000;' output/sh3/sh3.stack/kn-nginx/nginx.conf
test -f output/m1/m1.stack/kn-sftp/users.conf
"$ks" script fleet.yaml sh3 | head -1 | grep -q '^#!/usr/bin/env bash$'   # nothing but the script on stdout
"$ks" ca fleet.yaml | grep -q kiwiCA.pem
"$ks" modules | grep -q 'vpn-client'
"$ks" roles -v | grep -q 'vpn-server.wg_host'
if command -v shellcheck >/dev/null; then shellcheck -S warning output/*/*.stack/kiwi/gw.sh; fi
if command -v shellcheck >/dev/null; then shellcheck -S warning output/*/*.role.sh; fi
if command -v butane >/dev/null; then butane --strict output/sh3/sh3.bu >/dev/null; fi
# build must refuse politely without the tools, never half-build
if ! "$ks" build fleet.yaml lab1 --toolchain native 2>"$tmp/err"; then
    grep -qiE 'coreos-installer|not installed|missing' "$tmp/err"
fi
bash "$repo/install.sh" uninstall --purge >/dev/null
test ! -e "$ks"
echo "cli smoke test: ok"
