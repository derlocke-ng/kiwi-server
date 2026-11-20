# The control node

One node at home runs kiwi-server by itself, so the fleet needs no desktop
that is always on. It has a page for the phone: `https://control.<host>`,
open to the mesh only.

It checks every machine every ten minutes and sends the role to RAM masters
that booted. The page:

| | |
|---|---|
| **Home** | how the network is, in one line and four numbers (machines on, services running, the oldest last backup, devices); what wants a look and what that means; a picture of the network: who is linked to the master, its standby, the networks linked to this one, and a zone per site |
| **Machines** | every machine in a table by site, with a filter; add a machine; update everything |
| **Services** | every service in one table: whether it works and its last 90 days, how fast it answers, where it is open (VPN, Internet, Tor: the badge copies the address), and under its name **Settings** (what kiwi-server sets for it) and **Admin page** (the app's own, for what is inside it) |
| **A machine** | what it is, how busy and how full it is, what runs on it; Check now, Preview, Apply, each with what it does |
| **Devices** | add a phone or laptop to the VPN: a QR code for the WireGuard app and the config to download; *Only the network through the VPN* makes a split config (see [Joining the mesh](network.md#joining-the-mesh)) |
| **Accounts** | with the accounts module: requests to accept or deny, invite codes, the accounts and their password links, which services sign people in with them; Jabber's sign-up page too |
| **Linked networks** | from Home: what other networks may reach here, and whether this network leaves to the Internet through one of them |
| **Backups** | when each machine was last saved and where; bring a part of a machine back from a snapshot; fetch a copy of the network itself, or save one on the machines |
| **Computers** | who may manage the network; add one with a link |
| **Activity**, **Help** | what was done by whom; how do I…, and what the words mean |
| **The status page** | for everyone on the network, without a login, at `https://status.<host>`: every service with what it is for and whether it works, the game servers, how to get an account |
| **The status page for strangers** | under a public name (`control: { public_status_hostname: … }`): the services open to the Internet, whether they work, how to get an account |

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
hosts:
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
podman run -d --name kn-control --network host -v ~/kiwi-control:/fleet:Z --tmpfs /run/kiwi:size=2g,mode=0700 -e XDG_RUNTIME_DIR=/run/kiwi ghcr.io/derlocke-ng/kiwi-server-control:2 serve /fleet/fleet.yaml --listen 0.0.0.0:8740 --https 0.0.0.0:8743 --wait
```

```bash
podman logs kn-control
```

The last command shows a setup code and the fingerprint of the page's own certificate. Open
`https://<the machine's address>:8743` (the browser warns once: compare the fingerprint) from a
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
- Until the machine has its role, the page has a certificate of its own (made at each start, so the
  browser warns once; the screen shows its fingerprint to compare), and plain HTTP only sends the
  browser to it. The setup code, the password and the hoster's sign-in never travel unencrypted.
  Still, do the first run at home, not over a network you do not own.
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

A machine's page has tabs: Overview, Services (with the logins), History (what was done to it
and how it ended), Backups and Settings.

### The Machines table

Machines is a table with a row per machine. The masters and their standbys come first, as a
group of their own; the other machines are grouped by site (where they stand: `network.site`
in the fleet file). Each row has the machine's state, its role, its parts,
three small meters (CPU, memory, the fullest disk; point at one for the numbers), its last
backup and when it was last seen. A site's line says how its
machines are ("11 fine · 1 waits for unlock").

A machine has one state: fine, wants a look, has a problem, not reachable, waits for unlock
(its disk waits for its key), being installed, not set up yet, asleep, or not checked yet.

The counts above the table filter on a click. The filter is text, and it is part of the
page's address, so a view can be bookmarked:

| Filter | Shows |
|---|---|
| `state:down` | machines in that state: `bad`, `down`, `unlock`, `warn`, `installing`, `new`, `asleep`, `ok`, or a word of the state ("fine") |
| `site:main` | machines at a site whose name contains it (spaces as `-`) |
| `part:dns` | machines with that part, by its name or its title |
| `role:master` | machines with that role |
| `lime` | machines whose name contains it |

Several filters narrow the list; the same kind twice is either-or (`state:down state:bad`).

Tick machines (the box in a site's heading ticks the whole site) and a bar appears: Check,
Preview or Apply the ticked ones, one after the other.

### The Devices table

Devices lists every phone and laptop on the VPN in a table: its name, its group, its address
on the VPN, and whether everything or only the network goes through the VPN. With more than
one group, the counts above the table filter by group on a click. The filter is text in the
page's address, as on Machines: `group:staff`, a part of a name, or the start of an address
(`10.9.8`). Each row has *QR code* (shows the device's code and file again) and *Remove*.

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

The *Services* tab of a machine's page has a card *Logins* with one row for each service: its
name as a link to it, and its user name. *Show password* fetches the password
and *Copy* copies it. The password is not part of the page before you ask for
it. *Activity* then records that the logins of that machine were shown. It
does not record the passwords.

On the *Services* page, the link *login* beside a service leads to that card. What the list holds and where the passwords come from:
[Sign in to a service](machines.md#sign-in-to-a-service).

### A warning on purpose

Every problem a machine reports says what to do about it. A warning that is so on
purpose (an unencrypted disk on a machine that must start by itself, say) has
*Leave it out*: from then on it is a note on the machine's page, not a warning,
and no message is sent about it. It is the machine's setting `quiet` (*Warnings
left out*), which takes `disk-plain`, `data-disk-plain`, `clock`, `memory`,
`failed-units`, `unhealthy` and `not-applied`. Something broken never goes quiet.

### The role script

Below a machine's details, the button *The role script* opens what *Apply*
sends to that machine and runs there, as plain text. It is made when you open
the link, in the memory of the unlocked store, and is written nowhere. It
holds the machine's secrets, so keep it to yourself. *Activity* records that
it was shown. In the app: *Role script* in the machine's menu; in a terminal:
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

A login ends 30 minutes after you last did something on the page (its own
refreshes do not count), and 12 hours after you logged in whatever you do: a
browser left open at a computer nobody watches does not stay in.

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

With one ring the control node hangs on one machine. Name more, and any one of
them opens it:

```yaml
papaya:
  unlock: { control: [house, attic] }   # lychee's ring or medlar's: either opens it
```

Or ask for more than one at once, as a disk's `unlock.threshold` does
(clevis sss): with `control_threshold: 2` two of them have to answer together.

```yaml
papaya:
  unlock: { control: [house, attic, home], control_threshold: 2 }
```

Both are also settings of the machine on the page and in the app (*Disk unlock*).
A ring that does not answer when the key is made is left out with a note, as long
as enough others answer; the next login tries again. `kiwi-server recovery` counts each of them when it says
which machines come back by themselves after a power cut.
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

**What a computer asks the control node.** A computer that joined logs in with its
key (`POST /api/controller/hello`: the control node sends a challenge only that key
opens, and the answer is a token) and then sends `Authorization: Bearer <token>`:

| Route | What it gives |
|---|---|
| `GET /api/fleet`, `PUT /api/fleet`, `GET /api/fleet/revision` | the encrypted fleet, to fetch, to replace, its revision (in the activity log) |
| `GET /api/status` | the last look at the machines: `time`, `error`, and per machine `host`, `state` (ok, warn, bad, down), `problems` (each with `text`, `hint`), `notes` |
| `GET /api/machines` | what the fleet file says of the machines (`kiwi-server list --porcelain`), without the paths on the control node; `{}` while it is locked |
| `GET /api/activity` | the newest tasks and who did what |
| `POST /api/jobs` | starts an action as a job, as the page's buttons do: `{action, hosts}` (apply, update, status, plan, check, backup …) gives `{job}`; the History names the computer |
| `GET /api/jobs/{job}/log?offset=N` | the job's log from `offset` on: `text`, `offset`, `state` (waiting, running, ok, failed) |
| `GET /api/devices` | the phones and laptops on the VPN by name, the groups, the group a new one goes into |
| `POST /api/devices` | a new device, as the Devices page adds one: `{name, group, split}` gives `{name, config, qr, dns_search}`. `config` holds the device's private key and is shown this once; `dns_search` is the NetworkManager line's domains for a split config |
| `DELETE /api/devices/{name}` | takes a device off the VPN (a lost phone); the master forgets its key at once |
| `GET /api/settings?host=NAME` | the settings form of a machine, or without `host` of the defaults: its groups and fields as the page shows them (`name`, `type`, `label`, `value`, `inherited`, `set`, `options`, `help`); a secret field says only whether it is set |
| `POST /api/settings` | `{host, values: {field: value}, clear: [secret fields], apply}` gives `{saved, job}`: the same checks as the page, only what changed is written, `""` puts a field back to the defaults, a module list is a list of names; with `apply: true` the machines the change reaches are applied as a job. A wrong value gives 400 with `wrong: {field: why}` |
| `GET /api/access` | who may reach what, as the page shows it: `master`, `who` (each group and linked network with `kind`, `reach`, `peers`, `internet`, `admin`), `machines` |
| `POST /api/access` | `{who: {name: {reach, peers, internet, admin}}, new, new_as}` gives `{saved}`. What a name leaves out stays as it is; `reach` lists machines, services like `cloud.papaya`, ports like `papaya:22`, `apps` or `all`; `new` makes a group that starts with `new_as` (nothing, apps, all) |
| `GET /api/federation` | the linked networks: per name `domain`, `mesh`, `share`, `from`, `exit`, `internet` |
| `POST /api/federation` | `{name: {share, from, exit, internet}}`: what this network opens to a linked one, which of its people this network lets in, whether it leaves to the Internet through it. Linking a network needs both fleets and stays with `kiwi-server federate` |
| `GET /api/modules?check=1` | the module sources (`name`, `url`, `tag`, `commit`, `modules`); with `check`, `news`: each repository's newest version tag |
| `POST /api/modules` | `{op: add, update or remove, url, name, ref}` gives `{said}`: a module source, as on the Module sources page |
| `GET /api/accounts` | with the accounts module: `people` (name, display name, admin, Jabber), `invites` (never a code: a code is shown once when made), `waiting` (requests to accept or deny), `jabber`, and `error` when the accounts machine did not answer |
| `POST /api/accounts` | the Accounts page's buttons: `{op, name, hint, note, uses, days}` with `op` one of invite, accept, deny, revoke, create, link, delete, admins, games, connect (and `jabber-invite`, `jabber-accept`, `jabber-deny`, `jabber-revoke`) gives `{done, shown}`; `shown` is a new invite code or password link, given this once |
| `GET /api/hosts/{name}/logins` | a machine's logins with their passwords (`label`, `module`, `address`, `user`, `password`, `note`), never cached; the activity log says which computer was shown them |

`/api/status` and `/api/machines` also answer the page's login, only read, and are
asked at every refresh: they leave no line in the activity log. The Android app
(kiwi-app) reads its Home, Machines and Services from them.

**The registry, for everyone on the network.** A person who does not run the
network still wants to know what is in it for them. `https://status.<host>/api/registry`
gives, without a login, what the status page shows, as data for an app:

| Field | What it is |
|---|---|
| `kind`, `version` | `kiwi-registry`, 1 |
| `network`, `domain` | the network's name, its private domain (`home`) |
| `services` | each with `label`, `about`, `url`, `ok`, `uptime`, `onion`, and `android`: the package of its Android app, where there is one |
| `games` | each game server with `name`, `title`, `address`, `state` |
| `join`, `play`, `status` | the sign-up page, the game page, the status page |
| `ca`, `ca_mark` | the network's CA (`/api/ca`), from which the `.home` names' certificates come, and its mark: the first eight hex digits of its SHA-256, as an invite link ends with; the status page shows it too |
| `machine` | with `?machine=NAME`: that machine and whether it answers |

Every machine with a proxy leads there from `https://<its name>/.well-known/kiwi`
and from its LAN address (`https://192.168.2.61/.well-known/kiwi`): an app finds the
network from a name typed in, or by asking the addresses of the LAN. Under the
public name (`public_status_hostname`) the registry holds only what is open to the
Internet. kiwi-app's user side reads it.

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

Both merge the same way, also over `--ssh`; when a file changed on both sides they name it and
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

## One admin: applies run on the control node

A computer that joined lets the control node apply the machines: the app does so by
itself (*Preferences*, *General*, *Applying*), and in a terminal:

```bash
kiwi-server control run ~/kiwi/fleet.yaml apply papaya lychee
```

This copy goes to the control node first, the job runs there with the node's own
access to the machines, its log comes here as it is written, and what the job
changed comes back. Without a control node, or for a first apply over SSH, the
computer applies itself as before.

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

**The status page** is what everyone on the network opens first, without a login:
`https://status.<host>`. One page:
- every service people use by the name they know, what it is for and its address;
- "works" or "does not answer" and for how long, its last 90 days, how fast it answers;
- how many machines answer, when machines restart for updates, and what was wrong in the
  last seven days;
- its hidden services as Tor last found them;
- the game servers: up, asleep (the first player wakes it) or stopped, and where to connect,
  with a button to [the game page](#the-game-page);
- "Get an account", to the sign-up page when the network has the
  [accounts module](roles.md#accounts).

Each machine's own name leads to its part of the page: `https://lychee.home` shows
lychee's services, whether they work, and whether lychee answers, with a link to
the whole network. It shows no admin page and no setting, and says Kiwi: `brand_name` names the page for
the Internet only. Who may open it is set like for any service
([Who may reach what](network.md#who-may-reach-what)); `status_page: false` in the node's
`control:` block switches it off. Home shows the same to the people
who run the network, with the machine to open.

**Over Tor**, at the end of the status page: each hidden service with its Tor
address, and whether it answered when its machine last asked it through Tor
(every ten minutes, the way Tor Browser finds it). The status page can be a
hidden service itself: give the control node's machine the `onion` part with
`services: {status: {}}` and apply it; the page then shows its own Tor address.
A hidden service only for named keys is not asked (it needs one of them).

**For strangers**, `control: { public_status_hostname: status.example.org }` puts the
same page under a public name, with only the services that are open to the
Internet, whether they work, what was wrong with them, and how to get an
account. Nothing about the machines, the game servers or the VPN appears.

The network's own name and a few words about it go on the page for strangers;
its colour and how to reach its people on both (the page on the VPN says Kiwi, as
the control page does). The accounts module's `brand` names its sign-up page and
Kanidm's own pages, which show the kiwi too:

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

## A website for strangers

The control node can serve the network's website: a few pages of your own text, and what only
the network knows (the services open to the Internet and whether they work, the sign-up page,
the status page). The text lives in a git repository, pinned to one commit; start from the
template [dlhost-eu](https://github.com/derlocke-ng/dlhost-eu). In the fleet file, on the
machine with the `control` module:

```yaml
control:
  website_hostname: example.org
  website_repo: https://github.com/you/your-site
  website_commit: <40 hexadecimal digits>
```

The website, like the status page for the Internet, runs in a container of its own beside the
control part, with no fleet and no key in it (`<prefix>-control-public`).

Point `example.org` and `www.example.org` at the master in DNS, set `reverse-proxy: public:
true` on that machine, and apply the master and the machine. A change to the site is a commit, a
new `website_commit` and an apply. The pages are Markdown in a small, safe subset: HTML in them
is shown as text.

Images in a page are the site's own files: `![Our logo](assets/logo.png)`. An image from
elsewhere stays a link, so that no other server learns who reads the page. To serve the
files through a content delivery network (Cloudflare's proxy, say), point a name at the
master through it and set `website_cdn_hostname: cdn.example.org`: the pages then load their
images from there, only `/assets/` answers at that name, and each file's address carries the
pinned commit, so the CDN may keep it for good. The site says that the CDN sees the address
of whoever loads those files. With Cloudflare, its SSL mode *Full* takes the master's
certificate as it is.

To look at a site while writing it, run the page with `KIWI_SITE_HOST=localhost` and
`KIWI_SITE_DIR=<the folder>` in its environment and open `http://localhost:8740/`.

## The game page

People who are not admins can start and stop the game servers and read their
logs at `https://play.<control node>`, so a server runs only while someone plays.
They sign in with their account of the network ([accounts](roles.md#accounts));
only the members of one group get in, `games` unless you name another.

1. In the control node's settings (`control:` block): `play_page: true`. Apply it.
2. Connect the page to the accounts, once: on the Accounts page, **Game page
   (play)**, **Connect**; or

   ```bash
   kiwi-server accounts ~/kiwi/fleet.yaml connect papaya control
   ```

3. Let people in: the Accounts page has **Let in** at each account, the app a
   switch; or `kiwi-server accounts ~/kiwi/fleet.yaml member games alice`
   (`off` at the end takes them out).

The page lists every game server of the network with where players connect and
whether it runs, sleeps ([`sleep`](roles.md#game-servers)) or is stopped, with
**Start**, **Stop**, **Restart** and **Log**. `play_extra` adds other containers,
as `machine/container` (`papaya/kn-jellyfin`). Each click is a task of the control
node, in the machine's History with the person's name; one server takes a click
every 15 seconds.

It is on the VPN only, like every page here. Who of the VPN may reach it is set
as for any service ([Who may reach what](network.md#who-may-reach-what)). A public
name (`play_hostname: play.example.org` with `reverse-proxy: public: true`) opens
it to the Internet; the sign-in still decides who gets in. After the control
node restarts, the page waits until an admin opens the store.

## Mind

- Never put the module on a rented master: while it is unlocked, the machine
  holds every secret of the fleet in its memory.
- Nodes take SSH on their mesh address (`vpn-client: { mesh_ssh: false }`
  turns it off), keys only: that is how the control node reaches them from
  anywhere.
- `control: { allow_from: 192.168.0.0/16 }` opens the page to one more
  network, e.g. the home LAN.

Design: [design/control-plane.md](design/control-plane.md).
