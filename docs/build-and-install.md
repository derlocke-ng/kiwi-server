# Build and install machines

This page takes you from an empty folder to an installed machine. kiwi-server
reads your fleet file, writes a script and an install configuration for each
host, and builds an *unattended install ISO*: an install image that installs
the system without asking any questions. The machine's role, with its
services and their secrets, comes afterwards over SSH.

## Create a fleet

```bash
kiwi-server init fleet.yaml
```

`init` asks a few questions in the terminal: the domain, your SSH key, the
master and the machines at home. Pressing Enter takes the suggestion it found
on this computer, such as the time zone, the keyboard layout and your router's
network. It writes a fleet file that only waits for its master.
`init --example` writes the commented example fleet instead, and `--domain`
sets its domain. The app's *New fleet* asks the same questions.

Machines are named after fruit, and masters after fruit dishes:

```bash
kiwi-server add fleet.yaml node                       # the next free fruit, preset minimal
kiwi-server add fleet.yaml master                     # the next free fruit dish
kiwi-server add fleet.yaml node mybox --preset gateway --target debian
kiwi-server names                                     # free names, as JSON
```

[The fleet file](fleet-file.md) describes everything you can set.

## Check the fleet

```bash
kiwi-server validate fleet.yaml        # exit code 2 when something is wrong
kiwi-server list fleet.yaml            # every host, what has been generated and what comes next
kiwi-server show fleet.yaml papaya     # what one host ends up with, secrets masked
```

## Render and build

```bash
kiwi-server render fleet.yaml          # scripts and configs for every host
kiwi-server build fleet.yaml papaya    # render, then the ISO for papaya
```

Both commands take a list of hosts; without one they work on every host.
`build` needs the host's `disk:` setting, because the ISO wipes that disk.
Base images are downloaded once and cached in `~/.cache/kiwi-server`.

If the fleet's [secret store](security.md#the-secret-store) is open, the
rendered files go into the store's memory-only folder, because they contain
the fleet's secrets. Only the ISO is written to `output/<host>/` on disk. When
no store is used, everything goes to `output/<host>/`. `-o DIR` chooses
another folder.

| File | What it is |
|---|---|
| `<host>.role.sh` | the role as one script: the settings, the embedded files (the rendered stack among them), the common library and the role itself. Mode 0600, because it contains secrets. |
| `<host>.stack/` | for machines with modules: the rendered compose file, the module configs and the host units, for you to review. The script carries its own copy. |
| `<host>.bu`, `<host>.ign` | Fedora CoreOS and uCore: the Butane config (`fcos` 1.6.0) and the Ignition config made from it. |
| `<host>.preseed.cfg`, `<host>.kiwi-server/` | Debian: the preseed file, and the files the installer copies onto the new system. |
| `<host>.iso` | the unattended installer. |
| `README.txt` | a summary for that host. |

A Fedora CoreOS or uCore ISO is the stock live ISO after `coreos-installer iso
customize`, with the Ignition config embedded; the install needs no network.
A Debian ISO is the stock netinst image with the preseed file added to the
installer's initrd, so it answers every question from the first one on. Its
boot menu starts the unattended entry after a second.

## What the ISO contains

By default the ISO contains the base system only: the hostname, the network,
the admin user with your SSH keys, the time zone and the update policy. It
contains **none of the stack's secrets**. The role and its stack are sent
later by `apply`. The option `--bare` asks for exactly this; it is what `build`
does by default.

A node's ISO can be built before `enroll` has written its WireGuard
configuration; `apply` checks the role's settings when it sends them.

## Write the stick

```bash
sudo dd if=output/papaya/papaya.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

Write the image unchanged (`dd`, GNOME Disks' image writer or Fedora Media
Writer). Tools such as Ventoy, or Rufus in ISO mode, rewrite the image, and the
Fedora CoreOS live system then cannot find its root.

**Warning.** The ISO wipes the disk in the host's `disk:` setting as soon as a
machine boots from it, without asking. Label your sticks.

## Install

Boot the machine from the stick. It installs and then **powers off**. Remove
the stick, or detach the provider's ISO, before you start the machine again.
Media left first in the boot order would install again at every boot.
`install: { on_done: reboot }` in the fleet file makes the machine boot into
the installed system instead, which is useful for a VM whose ISO you detach
by hand.

When you start it again, the machine sets up its base system on its first
boot. uCore first reboots twice more to switch to its signed image. Follow it
with `journalctl -u kiwi-role -f`; the file `/var/lib/kiwi-server/role.done`
appears when it has finished. [First boot and updates](updates.md#the-first-boot)
says what happens.

## Apply the role

```bash
kiwi-server apply fleet.yaml papaya --ssh core@192.168.1.25
```

`apply` renders the role again, sends the script over SSH and runs it. The
machine starts its services, and `apply` checks that every container runs.
`--ssh` is only needed while the host's name does not resolve yet.
[Manage running machines](managing.md) explains `apply` and the commands
that follow.

## Offline media

A machine that cannot be reached over SSH can get everything on its install
media:

```bash
kiwi-server build fleet.yaml papaya --offline          # the role and its secrets on the ISO
kiwi-server script fleet.yaml papaya --offline > papaya.sh
```

The script runs with `sudo bash` on any installed Debian, Fedora CoreOS or
uCore machine. Without `--offline`, `script` prints the base system only.
Whoever holds an offline ISO or script holds the role's secrets. Keep it off a
provider's ISO store, and delete it once the machine is installed.

## Servers at netcup

kiwi-server can install a server at netcup through netcup's API, so you do not
have to use the web panel or a console:

```bash
kiwi-server vps login fleet.yaml             # prints a page to sign in on; needed once
kiwi-server vps list fleet.yaml              # your servers
kiwi-server vps probe fleet.yaml crumble     # its state, addresses, and what disk: has to be
kiwi-server build fleet.yaml crumble
kiwi-server vps install fleet.yaml crumble   # upload the ISO, boot it, wait, detach it, boot the disk
```

`vps install` wipes the server, so it asks first; `--yes` skips the question
in a script. It refuses an ISO that carries the role's secrets, and an ISO
built for another `disk:` than the server has. When it has finished, it
removes the ISO from netcup's storage and forgets the machine's old SSH host
keys.

A master is found by its `wg_host`. Any other host names its server with
`vps: { server: <id, name or nickname> }`, or you pass `--server`. netcup is
the only hosting provider kiwi-server can drive this way.

An encrypted Fedora CoreOS root needs about 4 GiB of memory at the first boot.
`vps install` refuses a smaller server for it; use `target: debian` there.
