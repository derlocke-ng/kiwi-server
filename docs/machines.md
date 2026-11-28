# Machines

## Targets

- `coreos`: Fedora CoreOS stable.
- `ucore`: CoreOS that rebases itself onto a `ghcr.io/ublue-os/ucore*` image,
  like the uCore project's own autorebase example.
- `debian`: trixie netinst, preseeded; `debian.arch: arm64` for arm64. A board
  that runs its own Debian or Armbian: [Boards](boards.md).
- `armbian`: a board's own Armbian image, ready to flash ([Boards](boards.md)).
- `coreos-ram`: a master that lives in RAM (see below).

## What you get

`kiwi-server render` and `build` write `output/<host>/`. While the secret
store is open it lives in the store's tmpfs: the rendered files hold the
fleet's secrets and are made again by every command, so they never touch the
disk. Only the ISO, and beside a bare one a `.bare` mark, stays on disk in
`output/<host>/`. A render removes the plain copies an older release left
there.

| file | what |
|---|---|
| `<host>.role.sh` | the role as one script: settings, embedded files (the rendered stack among them), the common library, the role body. Mode 0600 — it holds secrets |
| `<host>.stack/` | module roles: the rendered `docker-compose.yml`, the module configs (nginx.conf, post-rules.txt, gw.sh, start.sh, torrc, Pi-hole's records) and the host units — for review; the script carries a copy |
| `<host>.bu`, `<host>.ign` | CoreOS/uCore: Butane (fcos 1.6.0) and Ignition. Admin user, hostname, static network, time zone, update policy, `kiwi-role.service` with the script, uCore autorebase units |
| `<host>.preseed.cfg`, `<host>.kiwi-server/` | Debian: the preseed, and the files its late command copies in from `/cdrom/kiwi-server` — role script, `kiwi-role.service`, SSH keys, sudoers, sshd and update policy |
| `<host>.iso` | the unattended installer |
| `README.txt` | the same, for that host |

## Install media

- **CoreOS/uCore** media is the stock live ISO after `coreos-installer iso
  customize --dest-device <disk> --dest-ignition <host>.ign`. No network is
  needed for the install; Ignition is embedded for the installed system.
  The disk is encrypted: with the machine's rings when they answer at
  build time, else with its TPM standing in until the first apply binds the
  rings (or leaves the key for its screen). The install checks for a TPM 2.0
  and 4 GB of memory first and stops with what to do when one is missing;
  `encryption: false` on the machine installs it plain, by choice, and every
  page says so afterwards (docs/design/encryption.md).
- **Debian** media is the stock netinst with the preseed appended to the
  installer's initrd, so it answers from the first question on. The boot
  menus are rewritten to start the unattended entry after a second, and the
  host's files are under `/kiwi-server`. `xorriso -boot_image any replay`
  keeps the original BIOS/UEFI boot setup.

## First boot

`kiwi-role.service` is a oneshot that runs `/var/lib/kiwi-server/role.sh` as
root after the network is up, and never again once
`/var/lib/kiwi-server/role.done` exists.

- A failed run is retried on the next boot and shown on the console; the log
  is `/var/log/kiwi-server-role.log`.
- On uCore the unit also waits for `/etc/ucore-autorebase/signed`, so the
  role always runs on the final image.
- The script carries every secret of the machine, so it removes itself once it has
  run without a fault; one that failed stays for the next boot's try. To run the role
  again, apply the machine from your computer (`kiwi-server apply`): that sends a fresh copy.

## A second disk for the data

The install media take one disk, the system disk (`disk`), and erase it. A
machine with a second disk for the data says so in its settings ("Data disks"
in the app and on the page), or in the fleet file:

```yaml
hosts:
  papaya:
    disk: /dev/nvme0n1                  # the system: erased by the install media
    mounts: ["LABEL=data /mnt/data"]    # the data: mounted, never erased
```

An entry is the disk and where it goes. Name the disk by its label
(`LABEL=data`), its `UUID=…` or a `/dev/disk/by-id/…` path: `/dev/sda` can be
another disk after the next start. This lists what a machine has, with the line
for a disk that holds data and is not mounted:

```bash
kiwi-server disks ~/kiwi/fleet.yaml papaya
```

The disk is mounted at every apply, before the services start, and at every
boot. A machine installed anew finds its data disk again this way, with nobody
mounting it by hand. When the disk is not there, the apply stops and says so:
services writing into an empty folder on the system disk would look like lost
data later.

A new, empty disk needs a filesystem once: `"LABEL=…"` cannot name it yet, so
give its by-id path and a third word, `xfs` or `ext4`. That is only ever done on
a disk with nothing on it, and it is encrypted first, as the system disk is:

```yaml
    mounts: ["/dev/disk/by-id/ata-Samsung_SSD_870_S5Y1 /mnt/data xfs"]
```

Its key comes from the network's seed, so the machine installed anew opens the
same disk again; on the machine it is kept on the system disk, which is
encrypted itself, and the disk opens at every start after the system's. A fourth
word `plain` (or a machine with `encryption: false`) leaves it unencrypted. A
disk that already holds data is mounted as it is; `kiwi-server status` says when
a data disk is not encrypted. To encrypt one that holds data, copy the data off,
empty the disk (`wipefs -a`) and give its entry the format word.

## Updates

Updates are fetched continuously on every target; the *reboot* happens in
your window.

| target | installs updates | reboots |
|---|---|---|
| coreos | Zincati, continuously | only inside the window (`strategy = "periodic"`, local time) |
| ucore | `rpm-ostreed-automatic.timer` stages them (the image's default) | `kiwi-staged-reboot.timer` reboots into a staged deployment in the window |
| debian | unattended-upgrades, daily | `kiwi-reboot-if-required.timer` reboots in the window when `/var/run/reboot-required` exists |
| coreos-ram | a new ISO from `kiwi-server build`, booted (by hand for now) | the boot from it is the update, `watch` sends the role again; containers update in the window as everywhere |

`updates.days: []` means any day; `updates.enabled: false` turns all of it
off.

Two things keep a restart from taking a machine away:

- **No restart while the disk could not be opened again.** A machine whose disk
  opens through unlock rings asks them before it restarts for updates. When too
  few answer, it does not restart, says so in its journal, and tries again in
  the next window. Without this a machine restarts into a locked disk on the
  night the machine with its ring is off.
- **A power cut.** When the power comes back every machine starts at once, and a
  machine whose disk opens through a ring may start before the machine that serves
  the ring. It waits and asks again every few seconds, for as long as it takes; a
  ring that is silent is given up on after seconds and asked again. At least one
  machine with a ring must come up without needing a ring itself (a small board
  with no disk encryption does), or the home waits for someone to type a key.
  `tests/drills/power-cut.py` does this to a home: every machine off at once and on
  again. Tried on 15 November 2026 together with the master's server switched off at
  its provider (`master-gone.py`): the home was back by itself after 7 minutes, and the
  control node, once it was up, restarted the master and opened its disk: 12 minutes.
- **A hang is reset.** Where the machine has a hardware watchdog (most boards,
  mini PCs and virtual machines do), systemd feeds it. A machine that hangs is
  reset after a minute, one that hangs in the middle of a restart after ten.
  `watchdog: false` in the machine's settings switches it off.

## A master in RAM

`target: coreos-ram` ([design](design/ram-master.md)) is the CoreOS live ISO
with the master's live Ignition, booted every time; it touches no disk. The
image carries no role, only the machine's SSH host key.

After every boot the master waits, and `kiwi-server watch <fleet>` at home
sends the role and puts back the WireGuard identity and certificates the
fleet keeps of it. `watch --timer on` looks every minute while this computer
is on. `ram.copy_to_ram` is off by default (the image is read from the
medium); on, it is copied into RAM, which needs 3 GiB or more.

## Running machines

Once a machine runs, the fleet file stays the source. Change a setting, add
a module, renew a certificate, then:

```bash
kiwi-server apply fleet.yaml sh3            # re-render, send the role script over SSH, run it
kiwi-server apply fleet.yaml sh3 --no-run   # only put it at /var/lib/kiwi-server/role.sh
kiwi-server status fleet.yaml               # when each role was applied, uptime, kiwi-stack status
```

SSH goes to the admin user at the host's name. It uses the fleet's own key
when there is one (an `admin.ssh_keys` entry like `secrets/admin_ed25519.pub`
whose private key lies next to it) and your agent otherwise. Nothing asks for
a password; the install gave the admin user passwordless sudo.
`--ssh core@192.168.1.7` reaches a machine whose name does not resolve yet.

The script runs with `--force`, so the stack is rewritten and restarted with
whatever changed; the disk and the OS are not touched.

**Only the stack changed.** When the role script differs from the one the last apply sent only
in the stack's files, its containers' definitions or the network's names (a part's setting, a
new name the machine must know), only the stack's steps run: the names in `/etc/hosts`, the
files, a restart of the containers whose files changed, and the check that every container
runs. The machine's own steps (packages, users, firewall, its units, new images) are left out,
and the apply says "only its stack changed". `KIWI_FULL_APPLY=1` runs the whole role anyway.

**What starts again.** After an apply a container restarts when its definition or a file it
reads changed. A part's own script on the machine (a gateway's routes, the shared folders of a
Nextcloud, a sign-in set-up) runs again when a file of its own changed, and when it is not
running as it should. A setting of another part leaves it as it runs, so a gateway keeps its
routes and no backup starts.

**A new name restarts nothing.** A new site on a machine gives each of its containers one more
name, so that they reach the site through the machine's reverse proxy. The containers take the
name while they run. Only the reverse proxy restarts, because its configuration changed.
`kiwi-server plan` says how many containers take new names (on Podman; with Docker a container
whose names changed is still created anew).

**Nor in DNS.** The fleet's names reach Pi-hole as a file it reads again by itself, within a
second of the apply. A new or removed site restarts no Pi-hole, on no machine of the network.
`kiwi-server plan` marks such a file "read again while running". The first apply with 2.328.0
restarts Pi-hole once, because its settings change. Pi-hole's admin page no longer lists these
names under "Local DNS records".

**One login per machine.** An apply makes some twenty calls to a machine after its role; they share
one SSH connection, kept two minutes (a login is most of a call's second). A check whether a
machine answers, its boot system and a test of a new key log in on their own.
`KIWI_SSH_REUSE=0` logs in for every call. `KIWI_APPLY_TIMES=1` prints each call with its time,
and each step of the role on the machine that took half a second or more, with the names of the
files that changed. This is how to see where an apply's time goes; a trace of the role script
(`bash -x`) is switched off by the script itself, because it carries the machine's secrets.

**A change can reach other machines.** A new site on a node is a new name the
machines that answer for names (the master, a home gateway) must learn; a public
name is one the master hands on; a new backup client is one its backup server
must take. kiwi-server remembers what each apply sent and names every machine
whose role script is another now. To find them it renders only the machines a change can
reach: a machine's fingerprint holds its own settings, what every machine shares (names,
addresses, parts, the masters) and what its last render read of the other machines' parts. A
part's setting on one machine renders that machine alone; a comment in the fleet file renders
nothing:

```bash
kiwi-server apply fleet.yaml --changed          # every machine with changes not applied yet
kiwi-server apply fleet.yaml medlar --changed   # medlar, and every other one the change reached
```

**Several machines at once.** An apply of several machines goes in turns: the masters
first, one after the other; then the machines that answer for names or open disks
(`dns`, `tang`), together; then all the others together; the control node last when it
applies itself. `--at-once N` (or `KIWI_AT_ONCE`) limits how many run together. Each line of a machine's output starts with its name.

**A careful apply.** `--careful` is for a change that nobody tried yet, such as a new release
or a setting that reaches many machines:

```bash
kiwi-server apply fleet.yaml --changed --careful
```

It applies one machine after the other, the least important first: the machines nothing else
depends on, then the ones that answer for names or open disks, then the masters, and the
control node last when it runs the apply itself. Of two machines that share a moving address,
the one that waits goes before the one that holds it. After each machine its own sites are
asked on the machine until they answer, for up to two minutes, and its services until none
reports unhealthy. After a master, every site of the network is asked through it as well: its
VPN server may have restarted, and the tunnels take a moment to come back. The first machine that fails, whose sites do not answer or that has an
unhealthy service stops the apply, and the message names the machines that were not touched. A
machine that does not answer at all is left out and named at the end.

The app's **Apply** offers them along; the page has **Save & apply** in the
settings and **Apply all** on Home under "Changes not applied yet". The GUI has the same
two actions on the host page.

**The reverse proxy is asked first.** Before an apply writes anything of a machine's stack,
nginx tests the config it would get, in a throwaway container on the machine. If nginx refuses
it, the apply stops there with nginx's own words: "kn-nginx would not start with its new
settings". The machine's files and containers are the ones from before, and its sites go on
answering. Without this an nginx that refuses its config does not start again, and every site
of the machine is gone until the next good apply. The accounts (Kanidm) are asked the same
way.

**A machine whose settings cannot be read stops every apply** once it has been applied. Every
machine's role carries the names of the others (the name servers, the master), and a machine
whose settings this kiwi-server cannot read would be left out of them: its sites would be gone
from the network. Most often its settings come from a newer kiwi-server (on another computer,
or on the control node); the message then says to update kiwi-server here. A machine that was
never applied has no names yet and does not stop the others.

**A failed role says so**: `/var/lib/kiwi-server/role.failed` names the step
and the command, and `kiwi-server status` shows it. The marker that means
"applied" is only written once every container of the stack runs.

### One container

One container of a machine's stack can be restarted, stopped, started, or
show its last lines of log, without an apply:

```bash
kiwi-server service ~/kiwi/fleet.yaml papaya kn-nginx restart
kiwi-server service ~/kiwi/fleet.yaml papaya kn-vault logs --lines 50
```

The name is the container's, as `status` lists it: `kn-…` on a node, `km-…` on
the master. A stopped one stays stopped until you start it or the machine
restarts. The control page has the same buttons on the machine's page,
Services tab, **Its containers**; the app has them in the menu at each row of
**Services**. A restart from there is in the machine's History.

kiwi's own units on the machine take the same commands: the nightly update
(`kiwi-stack-update`), the runbook (`kiwi-runbook`), the role (`kiwi-role`).
`start` runs one that has finished once more, `logs` shows what it said:

```bash
kiwi-server service ~/kiwi/fleet.yaml papaya kiwi-stack-update logs
kiwi-server service ~/kiwi/fleet.yaml papaya kiwi-stack-update start
```

The page lists them under **Kiwi's own units**, the app under **Services**, folded
away unless one has failed.

### What a machine remembers

```bash
kiwi-server history ~/kiwi/fleet.yaml papaya --days 30
```

The machine's own journal: every apply (which kiwi-server, from which computer),
the nightly updates, what its runbook did, its backups and its starts. A failed
update or backup comes with its reason, the last error it wrote (a backup server
that refused it, for example). Applies made in a terminal are in it too, which
the control node does not see.

The app's History of a machine and the page's History tab show one list: what
the machine remembers and what was done through the control node (by whom), each
thing once, by day, newest first. **Changes**, **Backups** and **Problems**
narrow it down. The network's **Activity** is the same list for all machines.

### A shell on a machine

kiwi-server reaches its machines with a key and a list of host keys of its own
(`secrets/admin_ed25519`, `secrets/known_hosts`). For a shell of your own on one:

```bash
kiwi-server ssh-config ~/kiwi/fleet.yaml --write
```

That writes `~/.ssh/config.d/kiwi-<folder>.conf` with every machine: its admin user, its
port, the fleet's key and host keys. ssh reads the file once the first line of your
`~/.ssh/config` is `Include config.d/*` (kiwi-server says so and does not change that
file). Then `ssh papaya` works by the machine's name, and by an address when names do not
resolve: `papaya-lan` (its fixed address at home), `papaya-vpn` (on the mesh),
`sorbet-public` (a master's address on the Internet). A master's own name leads to that
address too: on the mesh its name is its VPN server, where no ssh answers. Without `--write` the text is
printed. It works while the fleet's secret store is open, and is made again after
machines or addresses change.

## Wake a machine that is off

A machine at home that is switched off can be started over the network
(Wake-on-LAN), when its card and its firmware allow it:

```yaml
hosts:
  fig:
    network: { dhcp: false, address: 192.168.1.40/24, gateway: 192.168.1.1, wol: true }
```

- The next apply switches waking on at the card (`WakeOnLan=magic` for systemd's
  network cards, and `ethtool` at once where it is there). It takes at the next start.
  Some machines also need it switched on in their firmware (BIOS/UEFI).
- Every apply learns the machine's card, its MAC and its address on the LAN, so nothing
  has to be typed. Before the first apply: `network.mac`.
- `kiwi-server wake fleet.yaml fig` has another machine of its LAN send the signal:
  one at the same site with an address in the same network, or the one named with
  `--via`. On the control page and in the app, a machine with `wol: true` that does not
  answer has **Wake it**.

The signal goes to the machine's address through a neighbour entry that names its MAC
for that moment: no broadcast is needed. `tests/drills/wake-netns.py` shows that the
packet reaches the card; that a card then starts its machine is up to the hardware,
and virtual machines cannot be woken this way.

Tried on 20 November 2026: an ODROID-N2+ with Armbian (kernel 6.18) takes the setting
(`Wake-on: g`, also after a restart) and gets the packet on its card, and stays off. Its
kernel says why at boot (`eth_wake_irq not found`): on this board the boot loader has to
arm the wake-up, and Armbian's does not. Such a machine needs its power button.

## Asleep by day, awake for the backups

A machine that is only needed at night, a backup machine for instance, can sleep the rest
of the day:

```yaml
hosts:
  lychee:
    asleep: { from: "06:00", until: "02:30" }
```

- From `from` it suspends to RAM (little power, its memory kept) and its own clock wakes it
  at `until`. The window may cross midnight; the times are the machine's own (`timezone`).
- It does not go to sleep while it is being set up, while its own backup runs or while a
  backup comes in to it; it tries again a quarter of an hour later, inside the window.
- `kiwi-server status`, the control page and the app say "asleep until 02:30" for it then,
  instead of a machine that does not answer, and no message goes out.
- Wake-on-LAN (above) wakes it earlier. In the settings: *Asleep from* and *…until*.
- Put the backups of the machines it keeps after `until` (the backup module's time).

## Sign in to a service

A service such as Nextcloud, Pi-hole or Portainer asks for a user name and a
password. `kiwi-server logins` lists them, machine by machine:

```bash
kiwi-server logins fleet.yaml               # every machine
kiwi-server logins fleet.yaml sh3           # one machine
kiwi-server logins fleet.yaml --porcelain   # the same as JSON
```

For each service the list has the address, the user name, the password and,
where one is needed, a note. A machine is listed with the modules it has in
the fleet file. A node that is not enrolled yet is left out, and the command
says so.

The passwords come from the fleet on this computer: the ones written in the
fleet file and the ones generated from the seed. The machine is not asked. A
password that somebody changed inside a service is therefore not known here.
Nextcloud, OpenCloud, Paperless and Portainer take their admin password only
when they start for the first time, and their note says so.

For some services the network keeps no password. In Vaultwarden everyone
makes their own account, and in Immich the first person to open it makes the
admin account. The note says how a person gets in.

The command prints the passwords on the screen. The control page and the
desktop app show the same list and keep a password hidden until you ask for
it: see [The control node](control.md#logins) and
[The desktop app](gui.md#a-host).

## People's computers

A person's computer (a desktop, a laptop, a pentest system on a portable SSD) can be a client of
the network. An agent on it tells the control node once a minute how the computer is, puts the
network's CA and VPN on it, and updates it when you ask. A client is the installed system, not
the box: the same SSD booted on two machines is one client on two boxes. The
[design](design/clients.md) says what comes next.

**Add one on the control page:** Devices, *Add a computer*. Give it a name, and leave *A place on
the VPN* ticked for a computer that goes out of the house. A pentest system belongs in a VPN
group of its own, which reaches the Internet and nothing at home. The page shows one command.
Run it in a terminal on that computer, at home or on the VPN. It asks for the computer's password
(`sudo`) and puts the agent on it. The command works once, within a day, and carries the
network's CA, so the computer checks that it talks to your control node. kiwi-server need not be
on the computer.

**Or from this computer:**

```bash
kiwi-server client add fleet.yaml desk --vpn
kiwi-server control push fleet.yaml
kiwi-server client install fleet.yaml desk --here
```

`--vpn` gives it its place on the VPN (`--group` its group; `--full` sends everything through the
VPN, not only the network). `install --here` puts the agent on this computer (`sudo` asks for
your password); `--ssh USER@ADDRESS` puts it on another one. `client invite fleet.yaml NAME` prints
the one-time command, as the page shows it. `client list` shows the clients with their last
report. `client remove` takes a client out of the fleet file and off the VPN.

**A computer leaves:** `sudo kiwi-client-agent leave` on it takes away what the agent put there:
its VPN connection, the network's CA, its disk's binding to the rings, and the agent itself.

**The app on the computer:** *Kiwi Agent* ([kiwi-agent](https://github.com/derlocke-ng/kiwi-agent),
installed with kiwi-updater: `kiwi add https://github.com/derlocke-ng/kiwi-agent.git`, then
`kiwi install kiwi-agent`) shows what the agent says: whether the network hears from the computer, its VPN connection (with
a switch), its disk, its updates. Its buttons do what the commands above do, as root through
polkit: *Join a network…* (paste the command from the page), *Bind…* (the disk's passphrase),
*Update now*, *Leave…*. The agent writes what the app shows into `/run/kiwi-client/status`,
without a secret in it. The agent needs no app: on a computer without a desktop it works the same.

The agent keeps itself current: when the control node has a newer one, it says so in the
computer's profile, and the agent fetches it, checks its signature and installs it. An agent of
2.330 cannot do that yet: run its computer's *Join command* (Devices) once more.

**A managed computer** (a company's): `client add NAME --managed` (or `kind: managed` in its
block). Its people log in with their accounts of the network (the accounts module, Kanidm), and
have no admin rights there; the network's admins get sudo. On the Accounts page, *Let log in* in
the *Computers* column says who may log in there (the Kanidm group `computers`; `login_groups` in
the block names others), and *Make admin* gives sudo (`admin_groups`). The agent sets the computer
up from its profile: Kanidm's own client (`kanidm-unixd`, layered on Bluefin and Silverblue, which
counts from the next start: the agent restarts the computer then, only while nobody is logged
in), Kanidm's authselect profile, and the sudo rule. A login is kept for when the computer is away
from the network. Install media for a managed computer create no local user. For Fedora-based
systems.

Its software: `apps` in its block lists the Flatpaks it has for everyone (from Flathub, like
`[org.mozilla.firefox, org.libreoffice.LibreOffice]`), and only the network's admins install or
remove software for the whole computer (`lock_software`, on unless set to `false`; a polkit
rule). A person can still install a Flatpak into their own home folder; locking that too comes
next.

Its desktop: `desktop_settings` sets GNOME's settings for everyone (dconf paths with their keys and
values as dconf writes them), and `desktop_locks` names the ones its people may not change:

```yaml
clients:
  office-1:
    kind: managed
    apps: [org.mozilla.firefox, org.libreoffice.LibreOffice]
    desktop_settings:
      org/gnome/desktop/screensaver: {lock-enabled: true, lock-delay: "uint32 0"}
    desktop_locks: [/org/gnome/desktop/screensaver/lock-enabled]
```

**A new computer from a stick:** `kiwi-server client iso` makes install media that installs
Silverblue, Bluefin or Bluefin-DX and joins the network at the computer's first start:

```bash
kiwi-server client add fleet.yaml laptop-anna --vpn
kiwi-server control push fleet.yaml
kiwi-server client iso fleet.yaml laptop-anna --system bluefin --base Fedora-Silverblue-45.iso
```

`--base` is Fedora's installer (Silverblue's ISO, or Everything's network install, from
fedoraproject.org). The stick installs the image straight from its registry, so the computer needs
the Internet during the install. It wipes every disk of the computer and encrypts the new one; the
installer asks for the disk's passphrase, and GNOME asks for its person at the first start.
Universal Blue's images (Bluefin) are switched to their signed origin after the install, so their
updates are checked against the signature. The stick carries a one-time join code that works for
14 days (`--days`), once: whoever has the stick within that time can join as that computer.
`--keymap` sets the keyboard of the install and of the disk's prompt; `--container` installs
another bootc image (`ghcr.io/ublue-os/aurora:stable`). `--passphrase-file` puts the disk's
passphrase on the stick: for test machines only.

**What the agent does:**

- It reports which box it runs on (a sum of the board's id, salted for this network), its
  system, its VPN links, whether its disk is encrypted and bound to rings, and its pending
  updates. The Devices page lists them under *Computers that report*, with the machine of the
  network whose hardware a computer runs on.
- It applies its profile when the network changes it: the network's CA in the system's trust
  store, and its VPN as a NetworkManager connection named after the network (`kiwi-home` for
  `home`) that comes up by itself. Only the
  network goes through it, and only the network's names are asked there, unless the computer
  was added with everything through the VPN.
- It takes two orders: *Update now* (rpm-ostree or apt) and *Apply its profile*, buttons on the
  Devices page, or `kiwi-server control order fleet.yaml NAME update`. Nothing else, never a
  command.

**Its disk:** a client with `unlock:` (`rings`, `threshold`, `switch`, as a machine's) gets the
rings' policy in its profile. Binding the disk needs its passphrase once: `sudo kiwi-client-agent
bind` at the computer installs clevis and its boot-system part, gives the boot system the network,
and asks for the passphrase. From then on the agent keeps the binding as the network says (other
rings, new keys) without the passphrase, and unbinds it when `unlock:` goes. The passphrase
always opens the disk too. Switching a computer's auto-unlock on the page, as a machine's: not built yet.

Everything it sends and gets is signed with a key of its own. Nothing listens on the computer.

## Remove a machine

```bash
kiwi-server remove fleet.yaml lime
```

The machine leaves the network for good. Its place on the VPN goes, so its key stops
working; its keys and rendered files on this computer go; its block is taken out of the
fleet file, and the comments of the other machines stay. The app's "Remove" and the
control page's "Remove this machine" do the same.

The command first names who still counts on the machine: a backup server that lists
it, a machine that sends its backups to it, a disk it helps unlock. Those settings are
yours to change. It refuses the last master of other machines and the last machine of
a network.

The machine itself is not touched. It still has what it had: wipe it, or switch it off.
What the network kept for it is set aside in the secret store, not deleted: the passwords of its
services (`secrets/pins.json`, under `removed/<day>/`) and its disk's keys
(`secrets/luks/removed/<day>/`). The disk still opens with that key until it is wiped. A machine
added later under the same name makes its own.
