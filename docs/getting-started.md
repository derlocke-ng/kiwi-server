# Getting started

To see first what a network can look like and what each machine in it does:
[Example setups](examples.md).

## What the machines need

Measured on the test network (November 2026), with a margin:

| Machine | Cores | Memory | Disk | Measured |
|---|---|---|---|---|
| Master (a rented server) | 1–2 | 2 GB | 20 GB | 0.5 GB memory and 4 GB disk in use |
| At home, for files, photos and passwords (Nextcloud, Immich, Paperless, Vaultwarden) | 4 | 8 GB, 16 GB for Immich's face search with room | 256 GB or more, or a second disk for the data | 4.3 GB memory and 28 GB for the system with 34 services |
| At home, smaller (Jabber, git, paste, a game) | 2 | 4 GB | 64 GB | 1.5 GB memory, 5 GB disk |
| Gateway (DNS, the way into the VPN) | 2 | 2 GB | 32 GB | 0.4 GB memory, 3 GB disk |

The big games want more: Enshrouded about 16 GB of memory ([roles.md](roles.md)).

## Without a Linux computer: the starter stick

A network can also start from one USB stick and a phone or a Windows PC. The
stick carries no secret; every copy is the same. Download it from the newest
release, with the file that checks it:

- https://github.com/derlocke-ng/kiwi-server/releases/latest/download/kiwi-starter.iso
- https://github.com/derlocke-ng/kiwi-server/releases/latest/download/SHA256SUMS

```bash
sha256sum -c SHA256SUMS     # "kiwi-starter.iso: OK"
```

On Windows: `certutil -hashfile kiwi-starter.iso SHA256`, and compare with the
line in `SHA256SUMS`. The release also has a signature made by the project's
build (`kiwi-starter.iso.sigstore.json`), for
`cosign verify-blob --bundle kiwi-starter.iso.sigstore.json --certificate-identity-regexp 'github.com/derlocke-ng/kiwi-server/' --certificate-oidc-issuer https://token.actions.githubusercontent.com kiwi-starter.iso`.
With kiwi-server installed, `kiwi-server build --starter` makes the same file
yourself.

Written to a stick (balenaEtcher, Rufus, or `dd`) and started on a machine with
a network cable, it does this by itself:

- A blank disk, and only one in the machine: it installs there.
- A disk that holds something (Windows, another Linux): it asks first, on the
  screen ("type ERASE") and on a small page at `http://kiwi.local`, with one
  button. Nothing is erased without that yes.
- Several disks: the page at `http://kiwi.local` shows them, one button each;
  the one you press is erased and installed on, the others stay as they are.
  (Or unplug the others and start again.)
- One disk that is too small (under 16 GB): it installs nothing and says so.
- The disk is encrypted. Until a network takes the machine in, its TPM opens
  it; then the network's rings do, or a key you type at its screen. A machine
  without a TPM 2.0 (look for TPM, fTPM or PTT in its firmware settings) or with
  less than 4 GB of memory is installed without encryption only when you type
  PLAIN (or press the page's button), and it says so on every page after.
- When an install stops, the page says why, with what the installer said.
- A machine that is installed already: it starts that system and says to take
  the stick out.

The installed machine shows where to open its page: `https://kiwi.local`, its
address, a setup code and a QR code, on its screen. The page asks the questions
of this chapter and makes the network ([control.md](control.md), "Start a
network from the page").

The machine from the stick is the network's first machine at home: the
questions list it as "This machine", it keeps the address the router gave it,
and nothing is installed again. When the master runs, "Set it up" on the page
gives it its services. The page then moves, like every service, onto the VPN:
`https://control.<name>.<domain>`. Put the phone on the VPN first (Devices).

Tried on 15 November 2026 in a virtual machine with a rented server as the
master (`tests/drills/starter-vm.sh first-node`): from the stick to a running
first node in a quarter of an hour, most of it the master's install; a hard
reset afterwards brought the node back as it was. Not tried yet: real hardware,
and logging in at the page's new address from a phone on the VPN. The sign-in at
the hoster on the page is the next step ([design](design/starter.md)).

## Make a fleet

```bash
kiwi-server init                # a few questions; the network goes into ~/kiwi/fleet.yaml
kiwi-server next ~/kiwi/fleet.yaml   # does the next step and says what it did; run it again
```

`init` first says what you need: a master (the one machine reachable from
the Internet, usually a small rented server), a machine at home for your
files, a USB stick. Then it asks three things: where the master runs, its
address on the Internet, and what the machine at home does. "No master yet"
is an answer too: the machines at home then answer at their own addresses,
at home only, and the first one answers for their names. A master can come
later ([A network without a master](design/no-master.md)). Everything else
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
- `kiwi-server add fleet.yaml master` adds a master; `--address` gives its
  public address. In a network without a master this also puts the machines
  at home onto its VPN
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
