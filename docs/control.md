# The control node

One node at home runs kiwi-server by itself, so the fleet needs no desktop
that is always on. It has a page for the phone: `https://control.<host>`,
open to the mesh only.

It checks every machine every ten minutes and sends the role to RAM masters
that booted. The page:

| | |
|---|---|
| **Home** | how the network is, in one line; what wants a look and what that means; every machine; a tile per service |
| **A machine** | what it is, what runs on it; Check now, Preview, Apply, each with what it does |
| **Devices** | add a phone or laptop to the VPN: a QR code for the WireGuard app |
| **Backups** | when each machine was last saved and where; bring a part of a machine back from a snapshot; fetch a copy of the network itself, or save one on the machines |
| **Computers** | who may manage the network; add one with a link |
| **Activity**, **Help** | what was done by whom; how do I…, and what the words mean |

Settings are changed on the page too: "Settings" on a machine's page, and "Settings
for every machine" on Home for what all of them inherit. They are the fields the app
shows, from one shared list. A change is checked before it is kept; "Apply" then
brings it to the machine. The app does the same from a computer and sends its changes
here by itself.

## Set it up

In the app: menu, **A home server that looks after it…**. Pick the home
server, type a password twice. Done.

By hand, the same steps:

```yaml
papaya:
  node:
    modules: [vpn-client, reverse-proxy, control]
```

```bash
kiwi-server secrets init ~/kiwi/fleet.yaml            # once: the secrets become the encrypted store
kiwi-server serve ~/kiwi/fleet.yaml --set-password    # the password that opens it on the node
kiwi-server apply ~/kiwi/fleet.yaml papaya            # the module
kiwi-server control push ~/kiwi/fleet.yaml            # the fleet, encrypted, to the node
```

Then, from a device on the VPN: `https://control.papaya.<domain>`. The first
login opens the fleet.

## Start a network from the page

A machine with nothing on it yet can ask the questions itself, the same ones as
`kiwi-server init` and the app. On any Linux machine at home with podman:

```bash
mkdir -p ~/kiwi-control
```

```bash
podman run -d --name kn-control --network host -v ~/kiwi-control:/fleet:Z --tmpfs /run/kiwi:size=2g,mode=0700 -e XDG_RUNTIME_DIR=/run/kiwi ghcr.io/derlocke-ng/kiwi-server-control:2 serve /fleet/fleet.yaml --listen 0.0.0.0:8740 --wait
```

```bash
podman logs kn-control
```

The last command shows a setup code. Open `http://<the machine's address>:8740` from a
computer in the same home network, answer the questions, type the code and choose a
password for the page. The page then shows the recovery key once: write it down.

- The code proves that the person at the page is the one who started it. Five wrong
  tries from one address and the page waits ten minutes.
- The new network is made in memory. On the disk are only the encrypted files
  (`fleet.yaml.age`, `secrets.age`); after a restart the password opens them.
- Until the machine has its role, the page is plain HTTP in your home network: do the
  first run at home, not over a network you do not own.
- A control node that waits for `kiwi-server control push` shows the same page; a push
  from the computer the network was made on takes its place.

### Then the machines

Home lists the machines that are still to set up, the master first, each with its next
step as a button:

| Step | What the button does |
|---|---|
| Make its install media | builds the machine's ISO (about 1 GB, a few minutes) |
| Fetch the install media | downloads it to your computer, for a USB stick or your hoster's ISO upload |
| Install it at netcup | for a master at a provider: makes the media, signs in (the task shows the page to open) and installs it there; everything on that server is erased |
| Give it its place on the VPN | a machine at home, once the master runs |
| Set it up | when the machine is installed and on: gives it its role |

"Add a machine at home" under the list adds another one. A machine without a fixed
address is set up at the address your router gave it; the page asks for it.

Install media hold the machine's keys, so they are made in memory and never on this
machine's disk: they are gone after a restart and after the machine is set up, and
"Make the install media again" brings them back. Two fit at a time with the control
module's default (`ram_mb: 2048`).

### The password is forgotten

"I forgot the password" on the login page takes the recovery key and a new password.

### Bring a network back from a copy

The same first page has a second way in, for a network that exists already and whose
computer or control node is gone: **I already have a network: bring it back from a
copy**, above the questions. It asks for five things:

| Field | What to give |
|---|---|
| The copy | a file named `fleet-….tar`: from "Fetch a copy" on the Backups page, from `kiwi-server backup --local`, or from one of your machines (`/var/lib/kiwi-server/backups`) |
| Recovery key | the one shown when the network was made; it starts with `AGE-SECRET-KEY-` |
| Setup code | from `podman logs kn-control`, as for a new network |
| A new password, twice | 12 characters at least |

The page then opens on Home, logged in, with the machines of the network.

- The copy is opened in memory. On the disk are only the encrypted files, as after a
  first run.
- The password of the page you had before stops working. So does the key of a
  computer's keyring that the store listed as `desktop`: a node has no keyring.
- A recovery key that does not open the copy, or a wrong setup code, brings nothing
  back. Five wrong tries from one address and the page waits ten minutes.
- A copy larger than 64 MB is refused.
- A copy of the older kind, `fleet-….tar.enc` with a passphrase, is not taken here.
  `kiwi-server restore` on a computer opens it.
- Files that `backup.extra` added to the copy are left out, because a control node
  keeps the store and the fleet file only. Activity says how many.

**A copy is not signed.** Someone who can replace the file where it is kept can put
another network in its place, and your recovery key would still open it. Look at the
machines' names on Home after a restore: they should be yours. [Backups](backups.md#what-a-copy-holds)
says what a copy holds.

## Secrets

Nothing rests on the node in the clear. Its disk holds `secrets.age` and
`fleet.yaml.age`, encrypted for the store's keys, and a salt.

| | |
|---|---|
| The password | makes an age key (scrypt, with the salt); that key is one of the store's keys. No hash of it and no key file is stored. |
| Logging in | opens the store and the fleet file into RAM inside the container. Job logs and rendered stacks stay in RAM too. |
| A restart, a power cut, **Lock** | locked again: the node does nothing by itself until the next login. |
| A stolen disk | holds encrypted files; each guess at the password costs scrypt (64 MiB). Use a long one: 12 characters at least. |

So after a power cut someone logs in once, from the phone. Or the node unlocks
by itself, with the help of another machine:

```yaml
papaya:
  unlock: { control: house }    # a Tang ring that another machine serves
```

After the next login the node keeps its key encrypted for that ring. After a
restart it asks the ring and opens with nobody there (seconds). If the ring's
machine is off it stays locked and the page says which machine it waits for.
A stolen disk alone opens nothing; a thief would also need to reach the ring
on your network, the same trade your encrypted disks make. The app's control
dialog offers this as "After a power cut".

## More computers

1. On the page: **computers**, **Add a computer**. Copy the link (it works once, for
   fifteen minutes).
2. On the new computer, in Kiwi Server: **Join a fleet**, paste the link.

That is all: the computer makes its own key (in its keyring), checks that the
link is from this fleet, fetches the fleet and opens it. Without the app:
`kiwi-server control join ~/kiwi/fleet.yaml <link>`.

**Remove** on the page takes its key out again: it reads nothing newer. What
it read before, it knows: see the next section.

## A computer was lost or stolen

Remove it (above), then replace what it knew. On the page, under
**Computers**; in the app, the menu's **A computer was lost or stolen…**

1. **The keys that open your machines.** Every machine gets a new admin SSH
   key and a new SSH CA. The old key keeps working until the last machine has
   changed, so a machine that is off does not lock you out: switch it on and
   do it again. Nothing restarts. A machine that is gone for good:
   **Leave it out** on the page, or `--waive NAME`. Something outside the
   fleet that you gave the fleet's key by hand (a desktop, a NAS) is not
   reached: put the new `secrets/admin_ed25519.pub` into its
   `authorized_keys` yourself.
2. **The secrets inside your services.** The fleet gets a new seed, and
   every machine is applied: the secrets its containers read at every start
   change (what Nextcloud's parts use among themselves, the ad blocker's and
   the VPN server's admin password, Paperless's session key, sftp and
   downloader passwords). Those containers restart; some ask you to sign in
   again. A password that lives in the service itself is changed there first
   when its module knows how (the databases of Immich, Paperless and Nextcloud so far);
   the others keep their value (`secrets/pins.json`) until their module can.
3. **The page's password.** Current password, new password, done. The old
   one opens nothing any more.

```bash
kiwi-server rotate ~/kiwi/fleet.yaml          # what can be replaced, and when it last was
kiwi-server rotate ~/kiwi/fleet.yaml ssh      # new SSH keys on every machine
kiwi-server rotate ~/kiwi/fleet.yaml seed     # a new seed, every machine applied
```

Not built yet: new database and admin passwords for the services, new VPN
keys for the machines, the key that unlocks disks at boot, a new
certificate authority. A key of your own in `admin.ssh_keys` is yours to
replace ([design/controllers.md](design/controllers.md)).

## Without a control node: a synced folder

Two computers can share a network through a folder that Nextcloud, Syncthing
or the like keeps the same on both. The network lies there encrypted: whoever
runs the syncing can delete it or put back an older copy, not read it.

1. On the computer that has the network: menu, **Keep it in a synced
   folder…** (`kiwi-server control push <fleet> --dir FOLDER`).
2. On the other computer: **Join**, name the folder. It asks to join
   (`kiwi-server control join <fleet> --dir FOLDER`).
3. The first computer shows the request the next time it syncs: **Accept** or
   **Deny** (`kiwi-server control requests|accept|deny <fleet> [name]`).
4. On the other computer, **Join** again: the network opens.

From then on both sync with that folder by themselves, like with a control
node. A folder caught in the middle of syncing is refused, not half taken.

## Staying in step

The app does it by itself: it fetches when a fleet is opened and sends after
a save. When two sides changed different things since they last agreed (you
added a device here, the control node learned a host key there), the two
become one copy by themselves. Only when both changed the same thing, the
settings file say, the app asks which one counts. By hand:

```bash
kiwi-server control pull ~/kiwi/fleet.yaml    # before working here
kiwi-server control push ~/kiwi/fleet.yaml    # after changing the fleet here
```

Both merge the same way; when a file changed on both sides they name it and
stop, and `pull --force` or `push --force` says which copy counts. The node
takes a push while it is open. A new password
(`--set-password`, then push) locks it until that password logs in.

## Mind

- Never put the module on a rented master: while it is unlocked, the machine
  holds every secret of the fleet in its memory.
- Nodes take SSH on their mesh address (`vpn-client: { mesh_ssh: false }`
  turns it off), keys only: that is how the control node reaches them from
  anywhere.
- `control: { allow_from: 192.168.0.0/16 }` opens the page to one more
  network, e.g. the home LAN.

Design: [design/control-plane.md](design/control-plane.md).
