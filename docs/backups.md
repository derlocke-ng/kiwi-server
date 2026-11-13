# Backups

A Kiwi Network has two kinds of backups. The **fleet backup** keeps the fleet
folder on your computer, which is the one thing the machines cannot rebuild.
The **`backup` modules** keep each machine's own data, such as Nextcloud's
files or Vaultwarden's database. This page explains both, and how to bring
them back.

## The fleet backup

The fleet folder holds the fleet file, `secrets/` (or the secret store) and
the CA. With a `backup:` block in the defaults, every successful `render` or
`build` sends it, encrypted, to the nodes you name:

```yaml
defaults:
  backup: { hosts: [m1, sh3], passphrase_file: ~/.config/kiwi-server/backup.pass }
```

The nodes keep the archives in `/var/lib/kiwi-server/backups`, the newest ten
of them. `--no-backup` on `render` or `build` skips it once.

```bash
kiwi-server backup fleet.yaml                  # by hand, to backup.hosts
kiwi-server backup fleet.yaml --local /media/usb   # also to a folder, such as a USB stick
```

On a fresh computer, `restore` brings the fleet back over SSH, before there
is any mesh:

```bash
kiwi-server restore --from core@192.168.1.5 --list           # what the node keeps
kiwi-server restore --from core@192.168.1.5 --into ~/kiwi    # the newest archive
kiwi-server restore FILE --into ~/kiwi                       # an archive you copied
```

The archive is a tar file encrypted by openssl with AES-256 and a key derived
from the passphrase. You can open it without kiwi-server:

```bash
openssl enc -d -aes-256-cbc -md sha256 -pbkdf2 -iter 600000 -in FILE | tar xz
```

## A machine's own data

Two modules keep a machine's data. `backup-server`, on a node with a big disk,
stores it. `backup`, on any machine, sends the machine's data there with
restic, encrypted, every night (at 03:00 unless set) and when you run
`sudo kiwi-stack backup`:

```yaml
hosts:
  lychee: { node: { modules: [vpn-client, reverse-proxy, backup-server],
                    backup-server: { clients: [papaya.home], data_dir: /mnt/backups } } }
  papaya: { node: { modules: [vpn-client, reverse-proxy, cloud, backup],
                    backup: { servers: [https://backup.lychee.home] } } }
```

Each module says itself what of it is worth keeping.

With two backup servers, list both:
`servers: [https://backup.lychee.home, https://backup.papaya.home]`. Each
server then holds a full copy of its own, and a restore takes the freshest.
Two backup nodes can each back up to the other.

### How a backup run behaves

- A database is paused while restic reads its files, for ten minutes at most.
- A run that fails is tried again after an hour, and then at longer intervals
  of at most eight hours.
- When the backup server is gone or full, the run stops instead of pausing
  each module in turn.
- `kiwi-server status` says when the last backup is late.

### A rebuilt machine

A rebuilt machine finds itself in the backups at its first apply, and
restores every module by itself before it backs up again.
`backup.restore: hold` makes it wait for `kiwi-stack restore` instead. A
rebuilt machine never backs up its empty self over its history;
`sudo kiwi-stack backup --new` starts a new history instead of restoring.

The service passwords are generated from the fleet's seed, so the seed
brings the backups back.

### Restore

On the machine:

```bash
sudo kiwi-stack snapshots [vault]                     # what is stored
sudo kiwi-stack restore vault cloud                   # the newest, stopping only those modules
sudo kiwi-stack restore --at "yesterday 18:00" vault  # the newest from that time or before
```

The time is the machine's local time unless it says otherwise; the list shows
UTC. From your computer, `kiwi-server backups` does the same:

```bash
kiwi-server backups fleet.yaml papaya                       # what the servers keep of papaya
kiwi-server backups fleet.yaml papaya --now                 # back it up now
kiwi-server backups fleet.yaml papaya vault --restore --at '2026-11-10 19:00'
```

The app has the same on each host's page.

### Prune old snapshots

A machine can add to its backups but not delete them, so a machine that is
taken over cannot destroy them. Old snapshots are removed from your computer,
which holds the keys:

```bash
kiwi-server prune fleet.yaml --dry-run      # only say what would go
kiwi-server prune fleet.yaml                # remove old snapshots now
kiwi-server prune fleet.yaml --every week   # a timer on this computer that does it
```

`prune` keeps what the backup server's `keep_daily`, `keep_weekly` and
`keep_monthly` say: seven days, four weeks and six months unless set.
`--keep-daily`, `--keep-weekly` and `--keep-monthly` override them once. With
`--every day` or `--every week`, a run that was missed comes at your next
login; `--every off` removes the timer. `kiwi-server status` shows on each
backup node when it was last pruned and how long its free space will last.

The keys of the Tang rings are kept by the `backup` module too; see
[Disk encryption](disk-encryption.md).
