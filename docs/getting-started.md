# Getting started

## Make a fleet

```bash
kiwi-server init                # a few questions; the network goes into ~/kiwi/fleet.yaml
kiwi-server next ~/kiwi/fleet.yaml   # does the next step and says what it did; run it again
```

`init` first says what you need: a master (the one machine reachable from
the Internet, usually a small rented server), a machine at home for your
files, a USB stick. Then it asks three things: where the master runs, its
address on the Internet, and what the machine at home does. Everything else
it finds on this computer or decides for you:

- the time zone, the keyboard and the language come from this computer;
- the address of a machine at home is one that does not answer right now, so
  it looks free;
- the install disk is `auto`: the installer takes the machine's one disk;
- the keys are made and encrypted, and the recovery key is shown once.

A wrong answer is asked again with a sentence that says what is wrong.
`init --all` also asks what has an answer that works (names, the system, the
disk, the name ending). `init --example` writes the commented example
instead. The app's **Start a new network** shows the same questions.

When the master runs at a hoster kiwi-server knows (netcup so far), `init`
offers to sign in there and lists your servers: you pick one, and its address
is not typed.

`next` is the guide: it takes the first machine that is not set up, the
master first, and does its next step (its place on the VPN, the install
media, the setup once the machine answers). Where a person has to act, such
as writing a USB stick, it says exactly what to do. `next fleet.yaml papaya`
takes that machine even when it was set up before: for one you installed
anew. By hand, the steps are:

```bash
kiwi-server list fleet.yaml     # every host, where it stands and what comes next
kiwi-server validate fleet.yaml
kiwi-server build fleet.yaml    # the ISOs (base images cached in ~/.cache/kiwi-server)
sudo dd if=output/sh3/sh3.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

New machines are named after fruits, masters after fruit dishes:

- `kiwi-server add fleet.yaml node` adds the next free fruit
- `kiwi-server add fleet.yaml master` adds a master
- `kiwi-server add fleet.yaml node mybox --preset gateway` uses a name of
  your own
- `kiwi-server names` prints the suggestions

What goes into the file: [The fleet file](fleet.md).

## Install a machine

Boot the machine from the ISO. **It wipes the machine's disk without
asking.** `disk: auto` takes the machine's one disk and stops when there are
several; a device such as `/dev/nvme0n1` names it.

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
