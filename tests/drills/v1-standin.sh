#!/bin/bash
# A stand-in for a v1 Nextcloud, for rehearsing its takeover into v2 (notes/migration.md): AIO's
# own containers under Docker, set up the manual way (github.com/nextcloud/all-in-one,
# manual-install), with the parts sh3's v1 has switched on, its own secrets, and a user with
# files. Run as root on a throwaway uCore machine of the test fleet (27 Nov 2026: mulberry).
#   v1-standin.sh up     the containers; on uCore the data folder needs the containers' label
#                        once, or Nextcloud cannot read it: chcon -R -t container_file_t FOLDER
#   v1-standin.sh data   a user "alice" with a note and a 3 MB file (its checksum kept)
#   v1-standin.sh down   the containers go; the volumes and the data folder stay
# The takeover after it, as rehearsed: notes/migration.md, "Rehearsed".
set -eu
d=/var/lib/kiwi-v1; data=/var/mnt/v1-ncdata; dom=cloud.v1.test
profiles="--profile collabora --profile talk --profile imaginary --profile fulltextsearch --profile whiteboard"
occ() { docker exec -e OC_PASS="${OC_PASS:-}" -u www-data kiwi-v1-nextcloud-aio-nextcloud-1 php occ "$@"; }
case ${1:-} in
up)
    mkdir -p "$d" "$data"; cd "$d"
    systemctl start docker
    sysctl -q -w vm.max_map_count=262144
    [ -f compose.yaml ] || curl -fsS -o compose.yaml https://raw.githubusercontent.com/nextcloud/all-in-one/main/manual-install/latest.yml
    [ -f sample.conf ] || curl -fsS -o sample.conf https://raw.githubusercontent.com/nextcloud/all-in-one/main/manual-install/sample.conf
    if [ ! -f .env ]; then
        r() { head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
        while IFS= read -r line; do
            k=${line%%=*}
            case $line in
                *"# TODO!"*) case $k in
                    NC_DOMAIN) echo "NC_DOMAIN=$dom" ;;
                    TIMEZONE) echo "TIMEZONE=Europe/Brussels" ;;
                    *) echo "$k=$(r)" ;;
                esac ;;
                COLLABORA_ENABLED=*|TALK_ENABLED=*|IMAGINARY_ENABLED=*|FULLTEXTSEARCH_ENABLED=*|WHITEBOARD_ENABLED=*) echo "$k=\"yes\"" ;;
                APACHE_PORT=*) echo "APACHE_PORT=11000" ;;
                APACHE_IP_BINDING=*) echo "APACHE_IP_BINDING=127.0.0.1" ;;
                NEXTCLOUD_DATADIR=*) echo "NEXTCLOUD_DATADIR=$data" ;;
                NEXTCLOUD_UPLOAD_LIMIT=*) echo "NEXTCLOUD_UPLOAD_LIMIT=50G" ;;
                NEXTCLOUD_MEMORY_LIMIT=*) echo "NEXTCLOUD_MEMORY_LIMIT=1024M" ;;
                NEXTCLOUD_TRUSTED_CACERTS_DIR=*) echo "NEXTCLOUD_TRUSTED_CACERTS_DIR=$d/cacerts" ;;
                "") ;;
                *) echo "${line%%  *}" ;;
            esac
        done < sample.conf > .env
        chmod 600 .env; mkdir -p "$d/cacerts"
    fi
    docker compose $profiles up -d
    ;;
data)
    # wait until Nextcloud is installed, then a user, a file of known content, a share, a calendar entry's app
    for i in $(seq 1 120); do
        [ "$(docker inspect -f '{{.State.Health.Status}}' kiwi-v1-nextcloud-aio-nextcloud-1 2>/dev/null)" = healthy ] \
            && occ status 2>/dev/null | grep -q "maintenance: false" && break
        sleep 10
    done
    occ status | sed 's/^/  /'
    export OC_PASS; OC_PASS=$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')
    occ user:info alice >/dev/null 2>&1 || occ user:add --password-from-env --display-name "Alice (v1)" alice
    printf '%s\n' "$OC_PASS" > "$d/alice.password"; chmod 600 "$d/alice.password"
    echo "written under v1 on $(date -u +%F)" > /tmp/v1-note.txt
    curl -fsS -o /dev/null -u "alice:$OC_PASS" -H "Host: $dom" -H "X-Forwarded-Proto: https" -T /tmp/v1-note.txt \
        "http://127.0.0.1:11000/remote.php/dav/files/alice/v1-note.txt"
    head -c 3000000 /dev/urandom > /tmp/v1-big.bin; sha256sum < /tmp/v1-big.bin | cut -d' ' -f1 > "$d/big.sha256"
    curl -fsS -o /dev/null -u "alice:$OC_PASS" -H "Host: $dom" -H "X-Forwarded-Proto: https" -T /tmp/v1-big.bin \
        "http://127.0.0.1:11000/remote.php/dav/files/alice/v1-big.bin"
    rm -f /tmp/v1-note.txt /tmp/v1-big.bin
    occ files:scan --quiet alice || true
    echo "files of alice: $(occ files:scan alice 2>/dev/null | grep -c . || true)"; ls -la "$data/alice/files" | tail -3
    occ app:list --shipped=false 2>/dev/null | sed -n '1,25p'
    ;;
down)
    cd "$d"; docker compose $profiles down      # the containers go, the volumes and the data folder stay
    docker volume ls --format '{{.Name}}' | grep nextcloud_aio | tr '\n' ' '; echo
    ;;
*) echo "usage: $0 up|data|down" >&2; exit 2 ;;
esac
