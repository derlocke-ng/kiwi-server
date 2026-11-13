# Getting started

## Make a fleet

```bash
kiwi-server init fleet.yaml     # asks: domain, your key, the master, the machines at home
kiwi-server list fleet.yaml     # every host, where it stands and what comes next
kiwi-server validate fleet.yaml
kiwi-server render fleet.yaml   # scripts + configs into ./output/<host>/
kiwi-server build fleet.yaml    # + the ISOs (base images cached in ~/.cache/kiwi-server)
sudo dd if=output/sh3/sh3.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

`init` asks a few questions in the terminal. Enter takes the suggestion it
found on this computer: time zone, keyboard, the router's network. It writes
a fleet that waits only for its master; `init --example` writes the commented
example instead. The GUI's *New fleet* asks the same questions, each with a
(?) for what it means.

New machines are named after fruits, masters after fruit dishes:

- `kiwi-server add fleet.yaml node` adds the next free fruit
- `kiwi-server add fleet.yaml master` adds a master
- `kiwi-server add fleet.yaml node mybox --preset gateway` uses a name of
  your own
- `kiwi-server names` prints the suggestions

What goes into the file: [The fleet file](fleet.md).

## Install a machine

Boot the machine from the ISO. **It wipes the disk named in `disk:`, without
asking.**

CoreOS and Debian install and then **power off** (`install.on_done:
poweroff`, the default). Remove the stick or the provider's ISO before the
machine boots again: media left first in the boot order would reinstall on
every boot. `install: { on_done: reboot }` goes straight into the installed
system instead.

Powered on again, the machine sets up its base system on first boot
(hostname, network, the admin user, the update policy). uCore reboots twice
more first (unsigned rebase, then signed). Watch it with
`journalctl -u kiwi-role -f`; `/var/lib/kiwi-server/role.done` appears when
it is finished. More: [Machines](machines.md).

## Apply the role

The ISO carries none of the stack's secrets. The role and its stack come
afterwards, over SSH:

```bash
kiwi-server apply fleet.yaml sh3 --ssh core@<address>
```

A node's ISO can be built before `enroll` wrote its WireGuard config; `apply`
checks the role's settings when it brings them.

## Offline media

For a machine that cannot be reached over SSH:

- `kiwi-server build fleet.yaml sh3 --offline` puts the whole role and its
  secrets on the ISO.
- `kiwi-server script fleet.yaml sh3 --offline > sh3.sh` writes the same thing
  as a script to `sudo bash` on any installed Debian / CoreOS / uCore machine.

Whoever holds one holds those secrets: keep it off a provider's ISO store and
delete it once the machine is installed.

## A server at netcup

No trip to the web panel: `kiwi-server vps` drives netcup's API.
`vps install` wipes the server, so it asks first (`--yes` for a script):

```bash
kiwi-server vps login fleet.yaml             # prints a page to sign in on; once
kiwi-server vps list fleet.yaml              # your servers
kiwi-server vps probe fleet.yaml crumble     # state, addresses, what disk: has to say
kiwi-server build fleet.yaml crumble
kiwi-server vps install fleet.yaml crumble   # upload, boot it, wait, detach, boot the disk
```

A master is found by its `wg_host`; any other host says `vps: { server: <id,
name or nickname> }`. `vps install` refuses an ISO that carries the role's
secrets, and one built for a different `disk:` than the server really has. It
removes the ISO from netcup's store when it is done, and forgets the
machine's old SSH host keys.
