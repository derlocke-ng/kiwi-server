# The control node

One node at home runs kiwi-server by itself, so the fleet needs no desktop
that is always on. It has a page for the phone: `https://control.<host>`,
open to the mesh only.

It checks every machine every ten minutes and sends the role to RAM masters
that booted. The page:

| | |
|---|---|
| **Home** | how the network is, in one line and four numbers (machines on, services running, the oldest last backup, devices); what wants a look and what that means; a picture of the network: who is linked to the master |
| **Machines** | every machine with its load, memory and disk; add a machine; update everything |
| **Services** | every service in one table: whether it works and its last 90 days, how fast it answers, where it is open (VPN, Internet, Tor: the badge copies the address), and under its name **Settings** (what kiwi-server sets for it) and **Admin page** (the app's own, for what is inside it) |
| **A machine** | what it is, how busy and how full it is, what runs on it; Check now, Preview, Apply, each with what it does |
| **Devices** | add a phone or laptop to the VPN: a QR code for the WireGuard app |
| **Accounts** | with the accounts module: requests to accept or deny, invite codes, the accounts and their password links, which services sign people in with them; Jabber's sign-up page too |
| **Linked networks** | from Home: what other networks may reach here, and whether this network leaves to the Internet through one of them |
| **Backups** | when each machine was last saved and where; bring a part of a machine back from a snapshot; fetch a copy of the network itself, or save one on the machines |
| **Computers** | who may manage the network; add one with a link |
| **Activity**, **Help** | what was done by whom; how do I…, and what the words mean |
| **The start page** | for everyone on the network, without a login, at `https://status.<host>`: every service with what it is for, its addresses and whether it works; the status page is at `/status` |
| **The front page for strangers** | under a public name (`control: { public_status_hostname: … }`): the services open to the Internet, how to get an account, and their status at `/status` |

The menu is in groups: Home, Machines and Services; *Access* (Devices, Who may reach
what, Linked networks); *Safety* (Backups, If something breaks, Computers); *More*.
On a computer the menu is at the side, with how the machines are below it; on a phone
it is a bar at the bottom, with the rest under "More". The page follows the device's
light or dark setting, and the moon button switches it (kept in a cookie of that
browser). Styles, drawings and the picture come from the node itself: nothing is
fetched from anywhere else, so the page works when the Internet is down.

Settings are changed on the page too: "Settings" in the menu or on a machine's page, and "Settings
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
- A master at netcup: *Sign in and pick it*, with the setup code, opens netcup's sign-in
  in a new tab. Once you confirm it there, the page lists the account's servers and the
  one you pick fills in the address. The login is kept with the network's sealed secrets,
  so the master can later be installed from the page without signing in again. Without
  it, type the address.
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

**A network without a master** ("No master yet" in the first questions): its machines
are off the VPN and answer at their fixed addresses at home; the first one answers for
the names. Away from home nothing opens. On Machines, "Add a master" takes its address
on the Internet, or signs in at netcup and lets you pick the server; the machines at home
then go onto its VPN, and Home lists the steps:
the master first, then each machine's place on the VPN and its setup.

**A machine started from the starter stick** needs no install media: start it on the
home network, and its screen shows its address and a setup code. On Machines, "A machine
with the starter stick is waiting": type both, say what it does, and "Take it in". The
code shows that you are at that machine; the control page hands it the network's key
and sets it up, and the starter's own page goes away. It keeps the address it has, as a
fixed one, and its disk is not encrypted (the starter installed it already). The app
does the same (menu, "Take in a waiting machine…"), and so does a terminal:

```bash
kiwi-server take-in ~/kiwi/fleet.yaml 192.168.1.40 --code abcd-1234 --preset cloud
```

Install media hold the machine's keys, so they are made in memory and never on this
machine's disk: they are gone after a restart and after the machine is set up, and
"Make the install media again" brings them back. Two fit at a time with the control
module's default (`ram_mb: 2048`).

### Logins

The page of a machine has a card *Logins* with one row for each service: its
name as a link to it, and its user name. *Show password* fetches the password
and *Copy* copies it. The password is not part of the page before you ask for
it. *Activity* then records that the logins of that machine were shown. It
does not record the passwords.

On Home, under *Open a service*, the link *login* below a service leads to
that card. What the list holds and where the passwords come from:
[Sign in to a service](machines.md#sign-in-to-a-service).

### The role script

Below a machine's details, the link *The role script* opens what *Apply*
sends to that machine and runs there, as plain text. It is made when you open
the link, in the memory of the unlocked store, and is written nowhere. It
holds the machine's secrets, so keep it to yourself. *Activity* records that
it was shown. In the app: *Show role script*; in a terminal:
`kiwi-server script FLEET HOST`.

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
   fifteen minutes). Or in Kiwi Server on a computer that has the network: menu,
   **Use another computer…**, **Add a computer** (`kiwi-server control invite <fleet>`).
   Both show the link as a QR code too.
2. On the new computer, in Kiwi Server: **Join a fleet**, paste the link. A link
   copied a moment ago is filled in already.

That is all: the computer makes its own key (in its keyring), checks that the
link is from this fleet, fetches the fleet and opens it. Without the app:
`kiwi-server control join ~/kiwi/fleet.yaml <link>`.

A computer at home that is not on the VPN stays in step too: when the control
page's name does not open there, the machine is reached at its fixed address at
home, which the fleet file knows.

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

## What the control node did

The page's *Activity* lists the newest tasks and who did what. A computer that
joined sees the same list in the app (menu, *Activity…*) or in a terminal:

```bash
kiwi-server control activity ~/kiwi/fleet.yaml
```

It holds names and times, no output and no secret.

## When a machine does not answer

The control node looks at every machine every ten minutes. One that does not
answer is named on Home, with how long it has been gone. By default nothing
more happens. The fleet file can allow more:

```yaml
defaults:
  recovery:
    master: { after: 15, do: restart }   # minutes; tell or restart
    node:   { after: 60, do: tell }
```

On the page this is "Settings for every machine", "When a machine is gone".

`restart` switches the machine off and on at its provider, and touches nothing
on it. It needs a machine at a provider and a control node that is signed in
there (`kiwi-server vps login`, see [getting-started.md](getting-started.md)). A machine at
home can only be told about.

The control node holds back in three cases, whatever the fleet file says:

- It restarted the same machine less than an hour ago.
- It restarted it three times in the last 24 hours. Then a person has to look.
- The provider does not answer. Then the control node itself may be the one
  without a connection, and it does nothing.

Every restart is in Activity.

A machine whose disk waits to be unlocked at boot counts as there, not as gone.
The control node forwards its unlock ring by itself, so a rented master with an
encrypted disk comes back after a restart with nobody there. The ring stays at
home; the control node only carries it to the machine's own address.
`recovery: { unlock: false }` switches that off, and "Unlock it" on the
machine's page does it by hand. A machine whose rings are all at home opens
when one of the machines that serve them is back.

One look by hand, without doing anything:

```bash
kiwi-server gone ~/kiwi/fleet.yaml --dry-run
```

What is planned beyond a restart: [design/recovery.md](design/recovery.md).

## The status page, and messages

After every look the control node also asks every service for a page, the way a
device on the network does. A machine can say that all its containers run while
a service answers nobody (a database that lost its settings in a power cut does
that); the question finds it. What is wrong is kept with its time.

**The start page** is what everyone on the network opens first, without a login:
`https://status.<host>`. Every service people use is a tile:
- what the service is for, and its address;
- its Tor address, when it has one anyone may open;
- whether it works, with its last 30 days.

"Get an account" leads to the sign-up page when the network has the
[accounts module](roles.md#accounts).

**The status page** is the detailed view, at `https://status.<host>/status`. Each service by the name people know,
"works" or "does not answer" and for how long, how many machines answer, when
machines restart for updates, and what was wrong in the last seven days. It shows
no admin page, no address and no setting. Who may open it is set like for any
service ([Who may reach what](network.md#who-may-reach-what)); `status_page: false`
in the node's `control:` block switches it off. Home shows the same to the people
who run the network, with the machine to open.

**For strangers**, `control: { public_status_hostname: status.example.org }` puts a
front page under a public name. It shows the services that are open to the
Internet, their web and Tor addresses, whether they work, and how to get an
account. The status of those services is at `/status`, and nothing about the
machines or the VPN appears.

The network's own name, colour, a few words about it, and how to reach its
people go on these pages:

```yaml
    control: { brand_name: dlhost, brand_accent: "#2f7d4a", about: "Services for our club and friends.", contact: admin@xmpp.example.org }
```

**Messages** go out when something has been wrong for ten minutes, and once more
when it is fine again: a machine or a service that does not answer, a problem a
machine reports (a full disk, a backup that is too old), and what the control
node did by itself (a master restarted, a disk opened). "Settings for every
machine" has the fields, under "Messages when something is wrong":

```yaml
defaults:
  notify:
    mail: { to: [me@example.org], host: smtp.example.org, user: me, password: "...", from: kiwi@example.org }
    ntfy: { url: https://ntfy.sh/a-name-only-you-know }     # a push message on the phone
    webhook: { url: https://example.org/hook }              # JSON: an SMS gateway, a chat room
    heartbeat: https://hc-ping.com/your-check               # asked after every look
    after: 10                                               # minutes
```

One, two or all three; a place that fails says why in Activity and does not stop
the others. "Send a test message" on the settings page tries them (in the app:
Defaults, under the messages, beside "Open the status page"), as does:

```bash
kiwi-server notify ~/kiwi/fleet.yaml --test
```

When the control node itself is off, it cannot say so. That is what `heartbeat`
is for: a service outside (healthchecks.io, or your own) that is asked after
every look and tells you when the asking stops.

## Mind

- Never put the module on a rented master: while it is unlocked, the machine
  holds every secret of the fleet in its memory.
- Nodes take SSH on their mesh address (`vpn-client: { mesh_ssh: false }`
  turns it off), keys only: that is how the control node reaches them from
  anywhere.
- `control: { allow_from: 192.168.0.0/16 }` opens the page to one more
  network, e.g. the home LAN.

Design: [design/control-plane.md](design/control-plane.md).
