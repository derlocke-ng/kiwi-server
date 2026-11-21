#!/usr/bin/env bash
# The backup store's copy at another place, for real (backup-server.offsite_url): a stand-in WebDAV
# server on one machine of the fleet (rclone serve webdav, with that machine's certificate from the
# fleet's CA), or a stand-in SFTP server (rclone serve sftp; its host key is kept at the first copy),
# the backup store of another machine copied there, then:
#   - the place holds no name it could read (no machine's name, no restic file name)
#   - the copy's names come back decrypted on the store's machine (backup-offsite.sh list)
#   - one machine's repository comes back from it byte for byte (backup-offsite.sh restore)
#   - a second run sends only what is new
#   - the repository taken out of the store comes back with kiwi-server backups STORE --from-offsite
# Afterwards the fleet file is as before (the offsite settings taken out again, the store applied)
# and the stand-in is gone.
#
#   tests/drills/backup-offsite.sh FLEET STORE DAV_MACHINE REPO [webdav|sftp]
#     STORE: a machine with backup-server; DAV_MACHINE: where the stand-in runs; REPO: a small
#     repository in the store to bring back (e.g. lime.home)
set -u
fleet=${1:?the fleet file}; store=${2:?the backup store machine}; dav=${3:?the machine for the stand-in}; repo=${4:?a repository}
kind=${5:-webdav}
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
if [ "$kind" = sftp ]; then
    url="sftp://$dav.$domain:18022/kiwi"
    # the stand-in on DAV_MACHINE, with a host key of its own
    at "$dav" "podman rm -f kiwi-drill-dav >/dev/null 2>&1; rm -rf /var/kiwi-drill-dav; mkdir -p /var/kiwi-drill-dav/kiwi
      podman run -d --name kiwi-drill-dav -p 18022:18022 -v /var/kiwi-drill-dav:/data:z $image serve sftp /data --addr :18022 --user drill --pass '$pw' >/dev/null
      sleep 3; podman ps --format '{{.Names}}' | grep -qx kiwi-drill-dav"
    check $? "a stand-in SFTP server runs on $dav ($url)"
else
    url="https://$dav.$domain:18443/kiwi"
    # the stand-in on DAV_MACHINE, with its own certificate (the fleet's CA signed it)
    at "$dav" "podman rm -f kiwi-drill-dav >/dev/null 2>&1; rm -rf /var/kiwi-drill-dav; mkdir -p /var/kiwi-drill-dav/kiwi
      d=\$(ls -d /home/*/docker/kn-nginx | head -1)
      podman run -d --name kiwi-drill-dav -p 18443:18443 -v \$d/fullchain.pem:/c.pem:ro,z -v \$d/privkey.pem:/k.pem:ro,z \
        -v /var/kiwi-drill-dav:/data:z $image serve webdav /data --addr :18443 --cert /c.pem --key /k.pem --user drill --pass '$pw' >/dev/null
      sleep 3; podman ps --format '{{.Names}}' | grep -qx kiwi-drill-dav"
    check $? "a stand-in WebDAV server runs on $dav ($url)"
fi
port=${url##*:}; port=${port%%/*}
# a DAV machine with firewalld (uCore, CoreOS): its port opened for this run only (runtime, not kept)
at "$dav" "! systemctl is-active -q firewalld || firewall-cmd -q --add-port=$port/tcp" >/dev/null 2>&1
# the store must reach it, or nothing below can work: said at once, not after half an hour of timeouts
(( $(at "$store" "if command -v curl >/dev/null; then curl -sk -m 8 -o /dev/null -w '%{http_code}' $url/ 2>/dev/null; \
     else timeout 8 bash -c 'echo > /dev/tcp/$dav.$domain/$port' 2>/dev/null && echo 1; fi" 2>/dev/null | tr -dc 0-9 | sed 's/^$/0/') > 0 ))
check $? "$store reaches the stand-in on $dav at port $port"
if (( fails )); then
    at "$dav" "podman rm -f kiwi-drill-dav >/dev/null 2>&1; rm -rf /var/kiwi-drill-dav; ! systemctl is-active -q firewalld || firewall-cmd -q --remove-port=$port/tcp" >/dev/null 2>&1
    say "stopped before anything was changed: the stand-in is gone again"
    exit 1
fi

# the store's copy there: the account in the fleet file, its password then into the secret store
PYTHONPATH="$here/lib" python3 - "$fleet" "$store" "$url" "$pw" "$S/ca/kiwiCA.pem" <<'EOF'
import sys, yaml
from kiwiserver import fleettext, util
p, store, url, pw, ca = sys.argv[1:]
text = util.read_text(p)
h = yaml.safe_load(text)["hosts"][store]
role = next(k for k, b in h.items() if isinstance(b, dict) and "backup-server" in (b.get("modules") or []))
b = dict(h[role].get("backup-server") or {})
b.update(offsite_url=url, offsite_user="drill", offsite_password=pw)
if url.startswith("https://"):
    b["offsite_ca"] = open(ca).read()
new = fleettext.put(text, ("hosts", store, role, "backup-server"), b)     # the comments stay
if new is None:
    sys.exit("the fleet file could not be changed in its text")
util.write_text(p, new, 0o600)
EOF
"${ks[@]}" secrets keep "$fleet" > keep.log 2>&1
! grep -q "$pw" "$fleet"; check $? "the account's password is in the secret store, not in the fleet file"
"${ks[@]}" apply "$fleet" "$store" > apply-on.log 2>&1; check $? "$store applied with the copy at another place"
t0=$(date +%s)
at "$store" "systemctl start kn-backup-offsite.service"; check $? "the first copy ($(( $(date +%s) - t0 )) s)"
sd=$(at "$store" "ls -d /home/*/docker | head -1")      # its stack folder (its admin user may be another)
at "$store" "test -s $sd/kn-backup-server/offsite-last"; check $? "it marks when the copy was last whole"
if [ "$kind" = sftp ]; then
    at "$store" "grep -q '^\[$dav.$domain\]:18022 ' $sd/kn-backup-server/offsite-known_hosts"
    check $? "the stand-in's host key is kept on $store"
fi
names=$(at "$dav" "find /var/kiwi-drill-dav/kiwi -printf '%f\n'")
n=$(printf '%s\n' "$names" | wc -l)
! printf '%s\n' "$names" | grep -qiE "\.home|$domain|snapshots|index|keys|config|data"
check $? "the place holds $n names and none it could read"
listed=$(at "$store" "bash $sd/kiwi/backup-offsite.sh list")
printf '%s\n' "$listed" | grep -qx "$repo/"; check $? "on $store the copy's names come back ($(printf '%s' "$listed" | tr '\n' ' '))"
at "$store" "rm -rf /var/kiwi-drill-back; bash $sd/kiwi/backup-offsite.sh restore $repo /var/kiwi-drill-back >/dev/null 2>&1
  cd $sd/kn-backup-server/data/$repo && find . -path ./locks -prune -o -type f -print0 | sort -z | xargs -0 sha256sum > /var/kiwi-drill-a
  cd /var/kiwi-drill-back && find . -path ./locks -prune -o -type f -print0 | sort -z | xargs -0 sha256sum > /var/kiwi-drill-b
  cmp -s /var/kiwi-drill-a /var/kiwi-drill-b"
check $? "$repo comes back from the copy byte for byte"
t0=$(date +%s)
at "$store" "systemctl start kn-backup-offsite.service"
check $? "a second copy ($(( $(date +%s) - t0 )) s): only what is new"
at "$store" "journalctl -u kn-backup-offsite.service -n 2 --no-pager -o cat" | sed 's/^/    /'
# a store installed anew: the repository taken out (kept beside it), back from the copy with kiwi-server
d=$sd/kn-backup-server/data
at "$store" "mv $d/$repo $d/.$repo.drill-kept"; check $? "$repo taken out of $store's store (kept beside it)"
"${ks[@]}" backups "$fleet" "$store" --from-offsite "$repo" > bring-back.log 2>&1; check $? "kiwi-server backups $store --from-offsite $repo"
at "$store" "cd $d/$repo && find . -path ./locks -prune -o -type f -print0 | sort -z | xargs -0 sha256sum > /var/kiwi-drill-c
  cmp -s /var/kiwi-drill-a /var/kiwi-drill-c"
check $? "$repo is back in the store byte for byte"
at "$store" "[ -d $d/.$repo.drill-kept ] && rm -rf $d/$repo && mv $d/.$repo.drill-kept $d/$repo"; check $? "$repo as it was"

# as before
PYTHONPATH="$here/lib" python3 - "$fleet" "$store" <<'EOF'
import sys, yaml
from kiwiserver import fleettext, util
p, store = sys.argv[1:]
text = util.read_text(p)
h = yaml.safe_load(text)["hosts"][store]
role = next(k for k, b in h.items() if isinstance(b, dict) and "backup-server" in (b.get("modules") or []))
b = {k: v for k, v in (h[role].get("backup-server") or {}).items() if not k.startswith("offsite_")}
new = fleettext.put(text, ("hosts", store, role, "backup-server"), b)
if new is None:
    # a block another command wrote in its own way (secrets keep, a quoted text over several lines): each
    # offsite_ key taken out with the lines below it that belong to it, the rest of the file as it was
    lines, out, skip = text.split("\n"), [], None
    for line in lines:
        ind = len(line) - len(line.lstrip())
        if skip is not None and (not line.strip() or ind > skip):
            continue
        skip = None
        if line.lstrip().startswith("offsite_") and ":" in line:
            skip = ind
            continue
        out.append(line)
    new = "\n".join(out)
    want = yaml.safe_load(text)
    want["hosts"][store][role]["backup-server"] = b
    if yaml.safe_load(new) != want:
        sys.exit("the fleet file could not be changed in its text: take the offsite_ settings out by hand")
util.write_text(p, new, 0o600)
EOF
"${ks[@]}" secrets keep "$fleet" >> keep.log 2>&1
"${ks[@]}" apply "$fleet" "$store" > apply-off.log 2>&1; check $? "$store applied without it again"
! at "$store" "systemctl cat kn-backup-offsite.timer >/dev/null 2>&1"; check $? "no copy timer is left on $store"
at "$dav" "podman rm -f kiwi-drill-dav >/dev/null 2>&1; rm -rf /var/kiwi-drill-dav"
at "$store" "rm -rf /var/kiwi-drill-back /var/kiwi-drill-a /var/kiwi-drill-b /var/kiwi-drill-c $sd/kn-backup-server/offsite-known_hosts"
at "$dav" "! systemctl is-active -q firewalld || firewall-cmd -q --remove-port=$port/tcp" >/dev/null 2>&1
say "$fails wrong"
exit "$fails"
