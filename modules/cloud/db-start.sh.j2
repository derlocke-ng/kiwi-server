#!/bin/sh
# Before Nextcloud's database starts: its settings file, put back when a power cut emptied it.
#
# The image rewrites postgresql.conf at every start (sed -i: a new file, renamed over the old
# one, nothing synced). A power cut in the seconds after that leaves the file empty. The
# database then starts with PostgreSQL's own defaults and listens on 127.0.0.1 only: Nextcloud
# waits for it for ever, and every container says it is healthy. The file holds no data: the
# image's sample is what a new database starts from, and the image's start script puts its
# own values into it again right after this.
d=/var/lib/postgresql/data
s=/usr/local/share/postgresql/postgresql.conf.sample
if [ -f "$d/PG_VERSION" ] && [ -f "$s" ] && ! grep -q '^listen_addresses' "$d/postgresql.conf" 2>/dev/null; then
    echo "kiwi: postgresql.conf has lost its settings (a power cut does that): putting the image's own back"
    cp "$s" "$d/postgresql.conf.kiwi" && chmod 0600 "$d/postgresql.conf.kiwi" && { sync "$d/postgresql.conf.kiwi" 2>/dev/null || sync; } \
        && mv "$d/postgresql.conf.kiwi" "$d/postgresql.conf" && { sync "$d" 2>/dev/null || sync; }
fi
exec /start.sh
