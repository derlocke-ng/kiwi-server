# Backups

Two things need a backup: the fleet directory on your computer, and each
machine's own data.

## The fleet directory

The fleet directory is the one thing the nodes cannot rebuild: the fleet
file, `secrets/`, the CA. With this in the defaults:

```yaml
backup: { hosts: [m1, sh3], passphrase_file: ~/.config/kiwi-server/backup.pass }
```

every successful render or build sends it, encrypted, to those nodes
(`/var/lib/kiwi-server/backups`, the newest ten kept).

- `kiwi-server backup` does it by hand, or to a USB stick with `--local`.
- On a fresh machine, `kiwi-server restore --from core@192.168.1.5 --into
  ~/kiwi` brings it back over plain SSH, before there is any mesh.
- The archive is openssl AES-256 with a passphrase-derived key, so this opens
  it without kiwi-server:

```bash
openssl enc -d -aes-256-cbc -md sha256 -pbkdf2 -iter 600000 -in FILE | tar xz
```

## A machine's data

Two modules do it. `backup-server` on a node with a big disk stores it;
`backup` on a machine sends it there with restic, encrypted, every night and
on `sudo kiwi-stack backup`:

```yaml
    lychee: { node: { modules: [vpn-client, reverse-proxy, backup-server],
                      backup-server: { clients: [papaya.home], data_dir: /mnt/backups } } }
    papaya: { node: { modules: [vpn-client, cloud, backup],
                      backup: { servers: [https://backup.lychee.home] } } }
```

With two backup nodes each backs up to the other, and every other machine to
both: `servers: [https://backup.lychee.home, https://backup.papaya.home]`
gives each server a full copy of its own, and a restore takes the freshest.

A rebuilt machine finds itself in the backups at its first apply and restores
every module by itself before it backs up again. `backup.restore: hold` makes
it wait for `kiwi-stack restore` instead.

## Restoring

- `sudo kiwi-stack snapshots [vault]` lists what is stored.
- `sudo kiwi-stack restore vault cloud` puts the newest back, stopping only
  those modules.
- `restore --at "yesterday 18:00" vault` takes the newest from that time or
  before, for a mistake found late. The time is the machine's own unless it
  says otherwise, and the list shows UTC.
- A machine that was rebuilt never backs up its empty self over its history;
  `sudo kiwi-stack backup --new` starts a history of its own instead of
  restoring.

The passwords derive from the seed, so the seed brings the backups back.

From your computer, `kiwi-server backups <fleet> <host>` lists what the
servers keep of a machine; `--now` backs it up, `--restore MODULE... [--at
TIME]` brings modules back. The GUI has the same on each host's page.

## Pruning

A machine can add to its backups but not delete them, so one that is taken
over cannot destroy them.

- `kiwi-server prune <fleet>` removes old snapshots from the operator's
  machine, which is where the keys are. It keeps what the backup server's
  `keep_daily`, `keep_weekly` and `keep_monthly` say: seven days, four weeks
  and six months unless set.
- `kiwi-server prune <fleet> --every week` sets up a timer on that machine
  that does it.
- `kiwi-server status` says on each backup node when it was last pruned and
  how long its free space lasts.

## When a run fails

A database is paused while restic reads it, for ten minutes at most. A run
that fails tries again an hour later, three more times. When the backup
server is gone or full the run stops rather than pause each module in turn.
`kiwi-server status` says when the last backup is late.
