# Backups

In the app: menu, **Back up the network now**, **Save a copy of the network…** and
**Restore a network from a copy…**. On the control page: **Backups**, with
**Save now** per machine and **Fetch a copy** for the network itself. The
commands below do the same.

Two things need a backup: the fleet directory on your computer, and each
machine's own data. What to do when something is lost, and a report that says
whether your backups would bring it back: [When something breaks](recovery.md).

## The fleet directory

The fleet directory is the one thing the nodes cannot rebuild: the fleet
file, `secrets/`, the CA. A backup of it is called a copy of the network.

```bash
kiwi-server backup ~/kiwi/fleet.yaml --local /run/media/you/stick
```

This writes one file, `fleet-<name>-<time>.tar`, into that directory. Name
machines and the copy goes to them, into `/var/lib/kiwi-server/backups`,
where the newest ten are kept (`backup.keep`):

```bash
kiwi-server backup ~/kiwi/fleet.yaml sorbet papaya
```

With this in the defaults, every successful render or build sends a copy to
those machines:

```yaml
backup: { hosts: [sorbet, papaya] }
```

On the control page, **Fetch a copy** on Backups downloads the file, and
**Save it now** puts one on every machine that is set up, or on
`backup.hosts` when the fleet file names them.

### What a copy holds

A network made by `kiwi-server init`, the app or the control page keeps its
secrets in the secret store, `secrets.age` ([Security](security.md)). Its
copy is a plain tar of these files:

| File | What it is |
|---|---|
| `MANIFEST.json` | the kind of copy, when and by which version it was made, and the sha256 of every file |
| `secrets.age` | the secret store as it rests in the fleet directory |
| `secrets.recipients` | the public keys the store is encrypted for |
| `fleet.yaml.age` | the fleet file |
| `federation.yaml.age` | the partner networks, when there are any |
| `<path>.age` | each file that `backup.extra` names |

Every `.age` file is encrypted with `age` for the store's keys. So the
recovery key opens a copy, as it opens the store. No passphrase is set or
asked for.

A copy and the recovery key are the whole network. Keep a copy somewhere
else than on your machines, and keep the recovery key apart from it.

`tar` and `age` open a copy without kiwi-server. `recovery.key` is a file
that holds the recovery key:

```bash
tar xf fleet-fleet-20261114T101500Z.tar
```

```bash
age -d -i recovery.key fleet.yaml.age
```

**A copy is signed.** `MANIFEST.json` finds a damaged copy, and its
signature a forged one: someone who can replace the file on a machine could
put another network in its place, encrypted for the same public keys, and your
recovery key would open it. The signature's key comes from the recovery key, so
`restore` checks it with the key you give, and refuses a copy that is not your
network's. A network made before 2.170.0 signs its copies once you give its
recovery key one time:

```bash
kiwi-server secrets copy-key ~/kiwi/fleet.yaml --identity recovery.key
```

An older copy of yours has no signature; `restore … --unsigned` opens it
(anyone could make a copy without one, so only for one you know is yours).

**The recovery key is lost, or someone saw it.** It was shown once, when the
store was made, and no command shows it again. While a computer still opens the
store, make a new one:

```bash
kiwi-server secrets recovery ~/kiwi/fleet.yaml
```

It shows the new key once and asks you to type its last 8 characters: only then
does it replace the old one (anything else, and nothing changes). With `--out
FILE` the key goes into a new file only you can read, never onto the screen.
The copies are signed with it from then on; the old key still opens the copies
made before, and nothing after.

### Bring it back

On a computer, from a file:

```bash
kiwi-server restore fleet-fleet-20261114T101500Z.tar --into ~/kiwi
```

Or from a machine that keeps copies, over plain SSH, before there is any
mesh:

```bash
kiwi-server restore --from core@192.168.1.5 --into ~/kiwi
```

The command asks for the recovery key. `--recovery-file FILE` or the
variable `KIWI_RECOVERY_KEY` give it without a prompt. `--list` shows what
the machine keeps, and `--name` picks one.

- A key that does not open the copy writes nothing.
- `--into` has to be a directory without a network in it.
- Afterwards the directory holds the store as it was and the fleet file as a
  plain file. The store is open.
- The computer gets a key of its own for the store, in its keyring, so the
  next `secrets unlock` needs no recovery key. Where no keyring takes a key
  the command says so, and unlocking asks for the recovery key.

Then look at what came back:

```bash
kiwi-server list ~/kiwi/fleet.yaml
```

The computer that was lost is still one of the ways to open the store.
`kiwi-server secrets status` names them, and `kiwi-server secrets remove`
takes one out.

A new control node takes a copy on its first page:
[Bring a network back from a copy](control.md#bring-a-network-back-from-a-copy).

The app's dialogs still ask for a passphrase. For a network with a secret
store the passphrase is not used when a copy is saved, and the box takes the
recovery key when a copy is restored.

### A fleet without a secret store

A fleet whose `secrets/` is a plain directory gets a copy of the older kind,
`fleet-<name>-<time>.tar.enc`: openssl AES-256 with a key derived from a
passphrase. The passphrase comes from `backup.passphrase_file`, from the
variable `KIWI_BACKUP_PASS`, from `--passphrase-file` or from a prompt.
Without one of the first two, a render or build warns and sends nothing.

`kiwi-server restore` opens both kinds and tells them apart by itself. This
opens the older kind without kiwi-server:

```bash
openssl enc -d -aes-256-cbc -md sha256 -pbkdf2 -iter 600000 -in FILE | tar xz
```

`kiwi-server secrets init` moves such a fleet to the secret store. Its next
copy is of the kind above.

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
TIME]` brings modules back, and `--new` starts the new history of a rebuilt
machine. The app has the same on each host's page; the control page has it
under Backups ("Bring something back", "Start new").

## Moving a part to another machine

```bash
kiwi-server move ~/kiwi/fleet.yaml git medlar lychee          # the plan, and what stands in the way
kiwi-server move ~/kiwi/fleet.yaml git medlar lychee --yes    # do it
```

A part (a module on a machine) moves with its data, its secrets and its name:

1. It stops on the machine it leaves, and that machine makes one last backup.
2. Its generated secrets are kept for the new machine (`secrets/pins.json`): a database
   password stays what the data was made with.
3. The fleet file: the part and its settings move to the new machine. It keeps its name
   (`git.medlar.home` stays, and now leads to the new machine; with
   [names by service](network.md#names-by-service) it is `git.home` before and after).
4. The new machine is applied and gets the part's data from the old machine's backups. The old
   machine's way into its backups goes over for that one run and is not kept there.
5. The old machine is applied, then every machine the change reaches (the ones that answer for
   names).
6. The new machine starts a backup: from then on the part is in the new machine's own store.
   Its history before the move stays in the old machine's.

The plan says what stands in the way: a machine without the backup part (the data travels
through the backups), a master and a node (they keep their parts' data under different
names), two machines with different stack folders, a part the target cannot run.

The part's data stays on the old machine's disk, unused. Delete it there when the part runs
well in its new place. If the move stops half way, it says how to go on by hand; the piece that
brings the data is

```bash
kiwi-server backups ~/kiwi/fleet.yaml lychee git --restore --from medlar
```

## Pruning

A machine can add to its backups but not delete them, so one that is taken
over cannot destroy them.

- `kiwi-server prune <fleet>` removes old snapshots from the operator's
  machine, which is where the keys are. It keeps what the backup server's
  `keep_daily`, `keep_weekly` and `keep_monthly` say: seven days, four weeks
  and six months unless set.
- `kiwi-server prune <fleet> --every week` sets up a timer on that machine
  that does it.
- "Prune now" does the same for one backup node: on the app's host page,
  and under Backups on the control page.
- `kiwi-server status` says on each backup node when it was last pruned and
  how long its free space lasts.

## When a run fails

A database is paused while restic reads it, for ten minutes at most. A run
that fails tries again an hour later, three more times. When the backup
server is gone or full the run stops rather than pause each module in turn.
`kiwi-server status` says when the last backup is late.
