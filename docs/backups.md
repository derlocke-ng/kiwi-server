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
(anyone could make a copy without one, so only for one you know is yours). The
app says so when it meets one and has *Open it anyway*; the control node's
first page has the tick *Open it without a signature*. A copy with a wrong
signature is never opened.

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

### A copy at another place

For 3-2-1 (three copies, two kinds of place, one elsewhere), a backup store can
keep a copy of everything it holds at another place. `offsite_url` says where:

| The place | `offsite_url` | `offsite_user` |
|---|---|---|
| 1&1 SmartDrive (WebDAV) | `https://sd2dav.1und1.de/kiwi` | the e-mail address or customer number |
| A Nextcloud (WebDAV) | `https://HOST/remote.php/dav/files/USER/kiwi` | the Nextcloud user (with a second login factor: an app password as the password) |
| Any other WebDAV folder | `https://HOST/PATH` | the account there |
| A Hetzner Storage Box (SFTP) | `sftp://uNNNNN.your-storagebox.de:23/kiwi` | `uNNNNN` |
| Any SFTP account (a friend's server) | `sftp://HOST:PORT/FOLDER` | the account there |

An SFTP folder is in the account's home. The place's host key is taken at the
first copy and kept in `offsite-known_hosts` beside the store, as ssh does: a
different key later stops the copy. Delete that file only when the place really
changed its key.

```yaml
hosts:
  lychee:
    node:
      backup-server:
        offsite_url: https://sd2dav.1und1.de/kiwi
        offsite_user: me@example.org
        offsite_password: ...          # kiwi-server secrets keep moves it into the store
```

Every night (`offsite_time`, 05:30) rclone brings the copy up to date: what is
new goes there, what was pruned goes there too. Before anything leaves, it is
encrypted once more with a key from the fleet's seed, names included, on top of
each machine's own encryption: the place sees files with names it cannot read,
not which machines they are of. `offsite_bwlimit` (say `5M`) keeps the home
line usable meanwhile; `offsite_ca` takes the CA of a server with a certificate
of its own.

On the store's machine, `kiwi/backup-offsite.sh list` shows what the copy holds
and `kiwi/backup-offsite.sh restore lime.home DIR` brings one machine's
repository back into a folder, as restic left it. The key comes from the seed,
so a network brought back from its copy can read the copy at the other place.

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

A part with a public name (`paste.example.org`: a name outside the network's domain, reached
from the Internet through the master) keeps answering after the move. The new machine's proxy
is opened to the Internet as the old one's is (`reverse-proxy: public: true`); the old one's
is closed when the part was its last public name. Once the master hands the name on, the new
machine asks Let's Encrypt for the certificate: allow a minute or two. A machine that gets no
certificate for a public name asks again three times, ten minutes apart, then once a day.

The plan says what stands in the way: a machine without the backup part (the data travels
through the backups), a master and a node (they keep their parts' data under different
names), two machines with different stack folders, a part the target cannot run.

The part's data stays on the old machine's disk, unused. Delete it there when the part runs
well in its new place. If the move stops half way, it says how to go on by hand; the piece that
brings the data is

```bash
kiwi-server backups ~/kiwi/fleet.yaml lychee git --restore --from medlar
```

## A standby for a part

A second machine can keep a part's data current and take the part over:

```yaml
hosts:
  quince:
    keep_ready:
      vault: medlar          # medlar keeps quince's vault ready
```

After an apply of both machines, medlar fetches the part's folders from quince every minute
(rsync over SSH). The part does not run on medlar, but it is made there: its images are
pulled and its containers prepared with quince's settings and secrets, without a site,
port or timer of their own. `kiwi-stack ready list` on medlar shows them. Its data waits
where the part looks for it.

```bash
kiwi-server standby ~/kiwi/fleet.yaml               # which part is kept ready where, and how old each copy is
kiwi-server switch ~/kiwi/fleet.yaml vault          # the plan
kiwi-server switch ~/kiwi/fleet.yaml vault --yes    # medlar takes the part over
```

A switch works whether quince answers or not. If it answers, the part stops there and medlar
fetches once more, so nothing is lost. If it does not, the part goes on with the copy medlar
has: at most the last minute is missing. Then, within seconds:

1. the containers kept ready start on medlar (`kiwi-stack ready start`), and medlar's proxy
   answers for the part's name (its certificate carries that name already);
2. every machine that answers for names points the name at medlar: they serve the names of
   parts kept ready from a file of their own, which the switch rewrites and Pi-hole reads at
   once.

The applies that follow make the part medlar's own. The part keeps its name and its secrets,
as in a move. quince gets the change the moment it answers again and then no longer runs the
part; from then on quince keeps it ready (the switch writes `keep_ready: { vault: quince }` on
medlar), so the two stay a pair and the part can go back the same way. A machine that answers for names and was not applied since the standby was set up
learns the new address with its apply.

Nobody has to be there: when quince is gone for five minutes and medlar cannot reach it
either, the control node switches the part by itself (see
[control.md](control.md#when-a-machine-does-not-answer)).

What to know:

- A folder a database lives in is read with the part's containers paused, for as long as
  the read takes (the same care the backup takes). With little change per minute that is a
  moment.
- Folders and named volumes are followed.
- Both machines must be of one kind (two nodes) with the same stack folder.
- The standby's key can do one thing on the machine that runs the part: read the folders
  kept ready (see [security](security.md#a-standbys-key)).
- A standby is not a backup: what is deleted on the first machine is deleted on the second
  a minute later.
- When a machine starts to keep a part ready over a folder that holds data already (it ran
  the part before a switch, and may have written after the copy it now fetches), that data
  is kept beside the folder as `<folder>.kept-<date>` first, for 14 days (hard links: little
  room), and the journal says so (`journalctl -t kiwi-standby`).
- A copy kept ready that is more than ten minutes old is a warning on the standby (the page,
  `status`, the messages), with what its last fetch said: a switch by itself would start
  with that copy.

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
