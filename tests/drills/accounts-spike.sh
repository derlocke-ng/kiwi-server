#!/bin/sh
# Drill: could Kanidm be the network's accounts? A throwaway Kanidm on one machine, reachable from
# that machine only (127.0.0.1), and what kiwi-server's sign-up would do with it:
#   1. an account made by the network (an invite code was right, or the people who run it said yes),
#   2. a link that lets its person set a password, handed over instead of an email,
#   3. Jabber's way in: a password checked over LDAP (right one in, wrong one out),
#   4. how much memory it takes.
#
#   ssh core@<machine> 'sudo sh -s' < tests/drills/accounts-spike.sh
#   ssh core@<machine> 'sudo podman rm -f spike-kanidm; sudo rm -rf /var/tmp/kiwi-spike/kanidm'   # afterwards
#
# Written 16 November 2026 (Kanidm 1.12.1 on papaya): all four passed, 59 MB.
set -e
D=/var/tmp/kiwi-spike/kanidm; mkdir -p "$D/cli"; umask 077
IMG=docker.io/kanidm/server:latest
cat > "$D/server.toml" <<'T'
bindaddress = "0.0.0.0:8443"
ldapbindaddress = "0.0.0.0:3636"
db_path = "/data/kanidm.db"
tls_chain = "/data/chain.pem"
tls_key = "/data/key.pem"
domain = "idm.spike.home"
origin = "https://idm.spike.home:18443"
T
podman run --rm -v "$D:/data:z" "$IMG" kanidmd cert-generate -c /data/server.toml >/dev/null 2>&1
podman rm -f spike-kanidm >/dev/null 2>&1 || true
podman run -d --name spike-kanidm -p 127.0.0.1:18443:8443 -p 127.0.0.1:13636:3636 -v "$D:/data:z" "$IMG" >/dev/null
sleep 8
# the admin that makes accounts (once: a second recovery changes the password again)
podman exec spike-kanidm kanidmd recover-account idm_admin 2>&1 | grep -o 'new_password[^,}]*' |
    sed 's/new_password[": ]*//; s/"//g' | tr -d ' \n' > "$D/idm_admin.pw"
K="podman run --rm --network host -e KANIDM_URL=https://127.0.0.1:18443 -e KANIDM_SKIP_HOSTNAME_VERIFICATION=true
   -e KANIDM_ACCEPT_INVALID_CERTS=true -v $D/cli:/root:z docker.io/kanidm/tools:latest kanidm"
KANIDM_PASSWORD=$(cat "$D/idm_admin.pw") $K login -D idm_admin </dev/null 2>&1 | tail -1
# 1, 2: the account, and the link its person sets a password or passkey with
$K person create alice "Alice Example" -D idm_admin </dev/null 2>&1 | tail -1
$K person credential create-reset-token alice --ttl 3600 -D idm_admin </dev/null 2>&1 | grep -c "ui/reset?token=" |
    sed 's/^1$/PASS  a link to set a password, no email needed/; s/^0$/FAIL  no reset link/'
# 3: Jabber over LDAP: a group with POSIX attributes, and the person's password for LDAP (set over the API:
# the command line asks for it on a terminal)
$K group create jabber -D idm_admin </dev/null >/dev/null 2>&1
$K group add-members jabber alice -D idm_admin </dev/null >/dev/null 2>&1
$K group posix set jabber -D idm_admin </dev/null >/dev/null 2>&1
$K person posix set alice -D idm_admin </dev/null >/dev/null 2>&1
python3 -c "import json; d=json.load(open('$D/cli/.cache/kanidm_tokens'));
print(next(t for v in d['instances'].values() for t in (v.get('tokens') or {}).values()))" > "$D/admin.tok"
head -c 18 /dev/urandom | base64 | tr -d '/+=' > "$D/alice.pw"
curl -sk -o /dev/null -X PUT -H "Authorization: Bearer $(cat "$D/admin.tok")" -H "Content-Type: application/json" \
    --data "{\"value\":\"$(cat "$D/alice.pw")\"}" https://127.0.0.1:18443/v1/person/alice/_unix/_credential
cat > "$D/bind.sh" <<'B'
apk add -q openldap-clients >/dev/null 2>&1
LDAPTLS_REQCERT=never ldapwhoami -x -H ldaps://127.0.0.1:13636 -D "name=alice,dc=idm,dc=spike,dc=home" -w "$1" >/dev/null 2>&1 &&
    echo "PASS  the right password over LDAP" || echo "FAIL  the right password over LDAP"
LDAPTLS_REQCERT=never ldapwhoami -x -H ldaps://127.0.0.1:13636 -D "name=alice,dc=idm,dc=spike,dc=home" -w "not-the-password" >/dev/null 2>&1 &&
    echo "FAIL  a wrong password over LDAP" || echo "PASS  a wrong password is refused over LDAP"
B
podman run --rm --network host -v "$D/bind.sh:/bind.sh:ro,z" docker.io/library/alpine:3.20 sh /bind.sh "$(cat "$D/alice.pw")" </dev/null
# 4
podman stats --no-stream --format "memory: {{.MemUsage}}" spike-kanidm
