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
- `sudo bash /var/lib/kiwi-server/role.sh --force` runs it again by hand.

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

**A change can reach other machines.** A new site on a node is a new name the
machines that answer for names (the master, a home gateway) must learn; a public
name is one the master hands on; a new backup client is one its backup server
must take. kiwi-server remembers what each apply sent and names every machine
whose role script is another now:

```bash
kiwi-server apply fleet.yaml --changed          # every machine with changes not applied yet
kiwi-server apply fleet.yaml medlar --changed   # medlar, and every other one the change reached
```

**Several machines at once.** An apply of several machines goes in turns: the masters
first, one after the other; then the machines that answer for names or open disks
(`dns`, `tang`), together; then all the others together, up to six at a time; the control
node last when it applies itself. Each line of a machine's output starts with its name.

The app's **Apply** offers them along; the page has **Save & apply** in the
settings and **Apply all** on Home under "Changes not applied yet". The GUI has the same
two actions on the host page.

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
