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
whatever changed; the disk and the OS are not touched. The GUI has the same
two actions on the host page.

**A failed role says so**: `/var/lib/kiwi-server/role.failed` names the step
and the command, and `kiwi-server status` shows it. The marker that means
"applied" is only written once every container of the stack runs.

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
