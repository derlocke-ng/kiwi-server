# Manage running machines

Once a machine runs, the fleet file stays the source for it. You change a
setting or add a module in the fleet file, and then apply it. This page explains the commands that work with running machines: `apply`,
`plan`, `status`, `check`, `enroll` and `mtu`, and the `kiwi-stack` command on
each machine.

## Apply changes

```bash
kiwi-server plan fleet.yaml sh3             # what an apply would change and restart
kiwi-server apply fleet.yaml sh3            # render again, send the role script over SSH, run it
kiwi-server apply fleet.yaml sh3 --no-run   # only put it at /var/lib/kiwi-server/role.sh
```

`apply` runs the role script again with `--force`. The stack is rewritten,
and on a podman stack only the containers whose files changed are restarted;
new ones are started. The disk and the operating system are not touched. When
it has finished, `apply` checks that every container of the stack runs, and
only then marks the role as applied.

`plan` is read-only. It compares the stack's files, quadlets and units on each
machine with what an apply would send, and says which containers it would
restart. Run it before an update. Right after an apply it says "nothing
changes".

The app has *Apply* on each host's page.

## How kiwi-server reaches a machine

kiwi-server connects over SSH as the admin user, to the host's name and its
`admin.ssh_port`. It uses the fleet's own key when there is one (an
`admin.ssh_keys` entry such as `secrets/admin_ed25519.pub` with its private
key next to it), and your SSH agent otherwise. Nothing asks for a password,
because the install gave the admin user passwordless sudo.

`--ssh core@192.168.1.7` reaches a machine whose name does not resolve yet.
After that, kiwi-server remembers the addresses at which it has met a machine,
so `.home` names do not need to resolve on your computer. The machines' SSH
host keys are kept in `secrets/known_hosts`; see
[Security](security.md#ssh-host-keys).

## See what the machines report

```bash
kiwi-server status fleet.yaml          # every host at once
kiwi-server check fleet.yaml           # after an update: names, DNS and the exit
```

`status` asks each machine over SSH, read-only, and shows when its role was
applied, its uptime, the state of its stack and every service, its disks, its
backups and its certificates, and what is wrong. A machine that is off does
not slow down the others. `--porcelain` prints the same as JSON; the app uses
it.

`check` runs from each master's Pi-hole container. It opens every name of the
fleet over HTTPS (a redirect, a login page or a refusal counts as an answer),
resolves one fleet name and one outside name, and checks the exit's address.
It is read-only and exits with 1 when something fails.

## Join the mesh

Every client of the mesh, whether a fleet host, a phone or a router, gets its
WireGuard configuration from the master's wg-easy. `enroll` talks to wg-easy's
API through an SSH tunnel to the master:

```bash
kiwi-server enroll fleet.yaml sh3                              # a fleet host: secrets/sh3.home.conf
kiwi-server enroll fleet.yaml phone --group devices --qr       # a device: secrets/devices/phone.conf and a QR code
kiwi-server enroll fleet.yaml laptop --group devices --split   # only mesh traffic goes through the tunnel
kiwi-server enroll fleet.yaml fritzbox --group routers         # a router; see Routers
kiwi-server enroll fleet.yaml sh3 --existing                   # fetch the configuration of a client that exists
```

A fleet host's configuration is picked up at the next render. Its client is
named after its hostname, so the wg-easy page and the fleet agree.
`vpn-server.wg_password` is the password `enroll` logs in with; it is
generated from the seed unless you set it.

`--group` puts the client into a group of the master. The group's subnet
decides the client's address, and the master's firewall decides by that
address what the client may reach. The default groups are:

| Group | Subnet | May reach |
|---|---|---|
| `guests` | `10.8.1.0/24` | the nodes, not other clients |
| `pentest` | `10.8.2.0/24` | nothing in the mesh, not other clients |
| `devices` | `10.8.3.0/24` | everything, including the admin pages |
| `routers` | `10.8.4.0/24` | everything |

`vpn-server.groups` changes them, and the app edits them as a list.
`--address` sets an address by hand. Nodes send all their traffic through the
tunnel. `--split` is for devices that should reach the mesh but keep their own
Internet connection. [Routers](routers.md) explains how a whole LAN joins the
mesh.

## Measure a tunnel's MTU

The MTU is the largest packet a connection carries in one piece. To find what
a path really carries, measure it from the node or the device:

```bash
kiwi-server mtu fleet.yaml m1 --ssh core@<address>          # a node
kiwi-server mtu fleet.yaml phone --ssh core@<master>        # a device from secrets/devices/
```

`mtu` sends pings that may not be split through the tunnel and reports the
largest size that arrives whole. If a device's path is small, change that
device's own configuration and leave everyone else alone. If a node's path is
small, `wg_mtu` has to come down on that node and its master; `--write` puts
that into the fleet file. A packet that is split on the way out and put
together again by the far end cannot be seen from here.

`wg_mtu` is 1376 unless set, and `validate` refuses a value whose packets do
not fit the stack network's `bridge_mtu`. The
[module reference](modules.md#what-the-renderer-adds-to-every-stack) explains
the two MTUs.

## On the machine: kiwi-stack

Every machine with modules has the command `kiwi-stack`, which runs as root:

| Command | What it does |
|---|---|
| `sudo kiwi-stack start`, `stop`, `restart` | starts or stops the whole stack |
| `sudo kiwi-stack status` | shows every container and host unit, then runs `verify` |
| `sudo kiwi-stack verify` | checks that every container of the stack runs |
| `sudo kiwi-stack update` | pulls new images and restarts what changed |
| `sudo kiwi-stack logs [service]` | follows the logs |
| `sudo kiwi-stack vpn-restart` | restarts the VPN client and the containers that share its network (Transmission, JDownloader) |
| `sudo kiwi-stack backup`, `snapshots`, `restore` | the machine's own backups; see [Backups](backups.md#a-machines-own-data) |

The stack also has timers for a daily VPN restart and a weekly image update,
set by the stack settings `daily_vpn_restart` and `weekly_update`.

**Warning.** Some containers share another container's network: Pi-hole, Tor
and nginx on a master, and the downloaders on a node. With
`runtime: docker`, never recreate one of them with
`docker compose up --no-deps`. Compose then gives it the literal network mode
`service:km-vpn-server`, which docker refuses to start. Remove it with
`docker rm` and run `docker compose up -d` without `--no-deps`, or use
`sudo kiwi-stack restart`.

## A failed role

When a role fails, the machine writes `/var/lib/kiwi-server/role.failed`,
which names the step and the command. `kiwi-server status` shows it. The
marker that means "applied" is only written once every container of the stack
runs.
