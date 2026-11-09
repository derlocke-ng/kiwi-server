"""The disk's one-time key.

A Debian install with `encrypt: true` formats the disk with a key that has to
be on the ISO, so the key must not outlive the install: kiwi-server makes a
short one per install (secrets/luks/<host>.once), and the first `apply`
replaces it with a generated one (secrets/luks/<host>.key) before anything
secret reaches the machine. A master also gets a new volume key: replacing a
passphrase keeps the old one, and whoever held the ISO and the disk during the
install, as a provider does, could otherwise still decrypt later snapshots.

Keys reach the machine on stdin, into a directory on tmpfs, never on a command
line. Design: docs/design/control-plane.md, Secrets.
"""
import os
import secrets
import sys

from . import remote, util
from .util import KiwiError

# Typed at a console whose keymap nobody can vouch for: no y or z (swapped on a
# German keyboard read as US), no l/1 or o/0, no punctuation.
ALPHABET = "abcdefghijkmnpqrstuvwx23456789"
ONCE_LENGTH = 14
KEY_LENGTH = 16

_PRELUDE = r'''
set -u
umask 077
d=$(mktemp -d /run/kiwi-luks.XXXXXX) || exit 1
trap 'rm -rf "$d"' EXIT
'''

# stdin: the one-time key, the key to replace it with, the key last given (may be empty).
# stdout: swapped|already|current <device>, or none.
SWAP_SCRIPT = _PRELUDE + r'''
IFS= read -r once; IFS= read -r next; IFS= read -r current
printf %s "$once" > "$d/once"; printf %s "$next" > "$d/next"; printf %s "$current" > "$d/current"
opens() { cryptsetup open --test-passphrase --key-file="$2" "$1" >/dev/null 2>&1; }
for dev in $(blkid -c /dev/null -t TYPE=crypto_LUKS -o device); do
    if opens "$dev" "$d/once"; then
        if ! cryptsetup luksChangeKey --batch-mode --key-file="$d/once" "$dev" "$d/next"; then
            echo "$dev: the key could not be replaced" >&2; exit 3
        fi
        if ! opens "$dev" "$d/next" || opens "$dev" "$d/once"; then
            echo "$dev: the replaced key did not check out" >&2; exit 3
        fi
        echo "swapped $dev"; exit 0
    fi
    if opens "$dev" "$d/next"; then echo "already $dev"; exit 0; fi
    if [ -s "$d/current" ] && opens "$dev" "$d/current"; then echo "current $dev"; exit 0; fi
done
echo none
'''

# stdin: the disk's key, the device. Resumes by itself if an earlier run was cut off.
REENCRYPT_SCRIPT = _PRELUDE + r'''
IFS= read -r key; IFS= read -r dev
printf %s "$key" > "$d/key"
cryptsetup reencrypt --batch-mode --progress-frequency 30 --key-file="$d/key" "$dev"
'''                          # not `exec`: the shell has to live on to run the trap that removes the key


def _path(fleet, name, kind):
    return fleet.resolve_path("secrets/luks/%s.%s" % (name, kind))


def _read(path):
    return util.read_bytes(path).decode().strip() if os.path.isfile(path) else ""


def _make(path, length):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    key = "".join(secrets.choice(ALPHABET) for _ in range(length))
    util.write_bytes(path, key.encode(), 0o600)
    return key


def uses_one_time_key(host):
    d = host.cfg["debian"]
    return host.target == "debian" and bool(d.get("encrypt") or d.get("partitioning") == "crypto") \
        and not d.get("passphrase")


def once_file(host):
    return _path(host.fleet, host.name, "once")


def install_key(host):
    """The key the installer formats the disk with: made on first use, the
    same until the machine's first apply replaces it."""
    path = once_file(host)
    key = _read(path)
    if not key:
        key = _make(path, ONCE_LENGTH)
        # stderr: `script` and --porcelain keep stdout for their payload
        print(":: made %s — %s's disk asks for this key at its first boot; the first apply replaces it"
              % (path, host.name), file=sys.stderr)
    return key


def current_key(host):
    """The key that opens the disk now, if there is one on record."""
    return _read(_path(host.fleet, host.name, "key")) or str(host.cfg["debian"].get("passphrase") or "")


def key_file(host):
    return _path(host.fleet, host.name, "key")


def make_key(host):
    return _make(key_file(host), KEY_LENGTH)


def wants_reencrypt(host):
    setting = host.cfg["debian"].get("reencrypt")
    return host.role == "master" if setting is None else bool(setting)


def swap(host, tgt):
    """At first contact, before the role's secrets reach the machine: the
    one-time key out, a generated one in. The new key is on disk here before
    the machine changes, and the one-time key stays until it has, so a run
    that is cut off can be repeated."""
    if not uses_one_time_key(host):
        return
    once = _read(once_file(host))
    if not once:
        return
    next_path, key_path = _path(host.fleet, host.name, "next"), _path(host.fleet, host.name, "key")
    new = _read(next_path) or _make(next_path, KEY_LENGTH)
    out = remote.run(tgt, SWAP_SCRIPT, ("%s\n%s\n%s\n" % (once, new, _read(key_path))).encode())
    words = out.decode(errors="replace").split()
    state = words[0] if words else "none"
    if state == "current":          # a render made a key for a later install: this disk is not it
        os.remove(next_path)
        return
    if state not in ("swapped", "already"):
        raise KiwiError("no disk on the machine opens with %s or the key on record — installed from an older ISO? "
                        "Build a new one, or remove that file if the key was changed by hand" % once_file(host))
    if wants_reencrypt(host):
        util.say("%s: giving the disk a new volume key, this takes some minutes" % host.name)
        remote.run(tgt, REENCRYPT_SCRIPT, ("%s\n%s\n" % (new, words[1])).encode())
    os.replace(next_path, key_path)
    os.remove(once_file(host))
    util.say("%s: the one-time disk key is replaced; the machine asks for the key in %s at every boot until it is bound to Tang"
             % (host.name, key_path))
