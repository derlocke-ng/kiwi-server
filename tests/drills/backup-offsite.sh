#!/usr/bin/env bash
# The backup store's copy at another place, for real (backup-server.offsite_url): a stand-in WebDAV
# server on one machine of the fleet (rclone serve webdav, with that machine's certificate from the
# fleet's CA), the backup store of another machine copied there, then:
#   - the place holds no name it could read (no machine's name, no restic file name)
#   - the copy's names come back decrypted on the store's machine (backup-offsite.sh list)
#   - one machine's repository comes back from it byte for byte (backup-offsite.sh restore)
#   - a second run sends only what is new
# Afterwards the fleet file is as before (the offsite settings taken out again, the store applied)
# and the stand-in is gone.
#
#   tests/drills/backup-offsite.sh FLEET STORE DAV_MACHINE REPO
#     STORE: a machine with backup-server; DAV_MACHINE: where the stand-in runs; REPO: a small
#     repository in the store to bring back (e.g. lime.home)
set -u
fleet=${1:?the fleet file}; store=${2:?the backup store machine}; dav=${3:?the machine for the stand-in}; repo=${4:?a repository}
here=$(cd "$(dirname "$0")/../.." && pwd)
ks=("$here/bin/kiwi-server")
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
dir=$(cd "$(dirname "$fleet")" && pwd); S=$(readlink -f "$dir/secrets")
read -r user domain < <(python3 -c '
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))["defaults"]
print(d["admin"]["user"], d.get("domain") or "home")' "$fleet")
lan() { python3 -c '
import sys, yaml
print(str(yaml.safe_load(open(sys.argv[1]))["hosts"][sys.argv[2]]["network"]["address"]).split("/")[0])' "$fleet" "$1"; }
at() {  # MACHINE COMMAND: as root there, by its home address
    ssh -i "$S/admin_ed25519" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$S/known_hosts" -o ConnectTimeout=10 "$user@$(lan "$1")" "sudo sh -c $(printf '%q' "$2")"
}
image=docker.io/rclone/rclone:1.75.2
pw=$(head -c 18 /dev/urandom | base64 | tr -d '/+=')
url="https://$dav.$domain:18443/kiwi"

# the stand-in on DAV_MACHINE, with its own certificate (the fleet's CA signed it)
at "$dav" "podman rm -f kiwi-drill-dav >/dev/null 2>&1; rm -rf /var/kiwi-drill-dav; mkdir -p /var/kiwi-drill-dav/kiwi
  d=\$(ls -d /home/*/docker/kn-nginx | head -1)
  podman run -d --name kiwi-drill-dav -p 18443:18443 -v \$d/fullchain.pem:/c.pem:ro,z -v \$d/privkey.pem:/k.pem:ro,z \
    -v /var/kiwi-drill-dav:/data:z $image serve webdav /data --addr :18443 --cert /c.pem --key /k.pem --user drill --pass '$pw' >/dev/null
  sleep 3; podman ps --format '{{.Names}}' | grep -qx kiwi-drill-dav"
check $? "a stand-in WebDAV server runs on $dav ($url)"

# the store's copy there: the account in the fleet file, its password then into the secret store
python3 - "$fleet" "$store" "$url" "$pw" "$S/ca/kiwiCA.pem" <<'EOF'
import sys, yaml
p, store, url, pw, ca = sys.argv[1:]
d = yaml.safe_load(open(p))
h = d["hosts"][store]
role = next(k for k, b in h.items() if isinstance(b, dict) and "backup-server" in (b.get("modules") or []))
b = h[role].setdefault("backup-server", {})
b.update(offsite_url=url, offsite_user="drill", offsite_password=pw, offsite_ca=open(ca).read())
head = "".join(l for l in open(p) if l.startswith("#"))
open(p, "w").write(head + yaml.safe_dump(d, sort_keys=False, allow_unicode=True, width=1000))
EOF
"${ks[@]}" secrets keep "$fleet" > keep.log 2>&1
! grep -q "$pw" "$fleet"; check $? "the account's password is in the secret store, not in the fleet file"
"${ks[@]}" apply "$fleet" "$store" > apply-on.log 2>&1; check $? "$store applied with the copy at another place"
t0=$(date +%s)
at "$store" "systemctl start kn-backup-offsite.service"; check $? "the first copy ($(( $(date +%s) - t0 )) s)"
at "$store" "test -s /home/$user/docker/kn-backup-server/offsite-last"; check $? "it marks when the copy was last whole"
names=$(at "$dav" "find /var/kiwi-drill-dav/kiwi -printf '%f\n'")
n=$(printf '%s\n' "$names" | wc -l)
! printf '%s\n' "$names" | grep -qiE "\.home|$domain|snapshots|index|keys|config|data"
check $? "the place holds $n names and none it could read"
listed=$(at "$store" "bash /home/$user/docker/kiwi/backup-offsite.sh list")
printf '%s\n' "$listed" | grep -qx "$repo/"; check $? "on $store the copy's names come back ($(printf '%s' "$listed" | tr '\n' ' '))"
at "$store" "rm -rf /var/kiwi-drill-back; bash /home/$user/docker/kiwi/backup-offsite.sh restore $repo /var/kiwi-drill-back >/dev/null 2>&1
  cd /home/$user/docker/kn-backup-server/data/$repo && find . -path ./locks -prune -o -type f -print0 | sort -z | xargs -0 sha256sum > /var/kiwi-drill-a
  cd /var/kiwi-drill-back && find . -path ./locks -prune -o -type f -print0 | sort -z | xargs -0 sha256sum > /var/kiwi-drill-b
  cmp -s /var/kiwi-drill-a /var/kiwi-drill-b"
check $? "$repo comes back from the copy byte for byte"
t0=$(date +%s)
at "$store" "systemctl start kn-backup-offsite.service"
check $? "a second copy ($(( $(date +%s) - t0 )) s): only what is new"
at "$store" "journalctl -u kn-backup-offsite.service -n 2 --no-pager -o cat" | sed 's/^/    /'

# as before
python3 - "$fleet" "$store" <<'EOF'
import sys, yaml
p, store = sys.argv[1:]
d = yaml.safe_load(open(p))
h = d["hosts"][store]
role = next(k for k, b in h.items() if isinstance(b, dict) and "backup-server" in (b.get("modules") or []))
b = h[role].get("backup-server") or {}
for k in [k for k in b if k.startswith("offsite_")]:
    b.pop(k)
head = "".join(l for l in open(p) if l.startswith("#"))
open(p, "w").write(head + yaml.safe_dump(d, sort_keys=False, allow_unicode=True, width=1000))
EOF
"${ks[@]}" secrets keep "$fleet" >> keep.log 2>&1
"${ks[@]}" apply "$fleet" "$store" > apply-off.log 2>&1; check $? "$store applied without it again"
! at "$store" "systemctl cat kn-backup-offsite.timer >/dev/null 2>&1"; check $? "no copy timer is left on $store"
at "$dav" "podman rm -f kiwi-drill-dav >/dev/null 2>&1; rm -rf /var/kiwi-drill-dav"
at "$store" "rm -rf /var/kiwi-drill-back /var/kiwi-drill-a /var/kiwi-drill-b"
say "$fails wrong"
exit "$fails"
