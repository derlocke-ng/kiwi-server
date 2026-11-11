# A master that lives in RAM

Designed and prototyped 11 November 2026 (`target: coreos-ram`). The master's system
starts from its image at every boot and writes only to RAM; what makes it *this* master
stays on one partition of its disk.

## The base: CoreOS live

The Fedora CoreOS live ISO is already that: Ignition runs at every boot, the root is a
read-only image, everything written goes to RAM. It has systemd, podman and quadlets, so
kiwi-stack and the role script run unchanged, and it is phoenix-server's lineage. Alpine
diskless is smaller but OpenRC, and every quadlet and host unit of kiwi-stack is systemd:
it would be a second machine side.

## The image

`kiwi-server build`: the stock live ISO with the host's live Ignition (`coreos-installer
iso customize --live-ignition`): admin user and keys, hostname, network, the units below.
No role and no secret, so a provider's media store may keep it. On netcup the ISO stays
attached and the machine boots from CD-ROM every time.

Measured with the master stack (wg-easy, Pi-hole, nginx): 535 MB used of 2 GiB.
`coreos.liveiso.fromram` would copy the image into RAM as well, about 900 MB more, and needs
3 GiB: with 2 GiB the initramfs runs out of space and stops in emergency mode. So it is the
setting `ram.copy_to_ram`, off by default.

## The state at every boot

The roadmap's objection: kept on the hoster's storage the state needs encryption again;
handed over at boot it needs someone. Neither holds:

- **No encryption again.** A master's disk is unencrypted by default since 10 November,
  because the hoster can read RAM anyway. A RAM master keeps the same data on the same
  disk under the same threat model, and less of it: no OS, no logs, no shell history.
- **Nobody at boot.** A unit reads the partition, not a person.

One partition labelled `kiwi-state` on `disk:`. `kiwi-state.service` mounts it at every
boot and binds it into place:

| On the partition | Bound to | What |
|---|---|---|
| `kiwi-server/` | `/var/lib/kiwi-server` | the role script `apply` sends |
| `docker/` | the stack directory | WireGuard keys and peers, certificates, Pi-hole, backup markers |
| `containers/` | `/var/lib/containers` | the image cache (`ram.image_cache`) |
| `journal/` | `/var/log/journal` | logs, only with `ram.keep_logs` |
| `ssh/` | `/etc/ssh/ssh_host_*` | the host keys, so `known_hosts` stays valid |

Then `kiwi-role.service` runs the role script from it, as on a first boot, and the stack
starts. The boot never formats anything: the first `apply` makes the partition (wiping
`disk:`, as every install does) before it sends the role. Without the partition (a new VPS,
a wiped disk) it is the disposable master the roadmap has: `apply` makes it, puts the
WireGuard identity back from the fleet's copy, and the backup restores the rest.

Not adopted: a blob sealed to a Tang ring (the hoster has the blob and reaches the ring
from the master's address, so it protects against nobody who matters); nothing on disk at
all (every reboot would need `apply`, with a new host key each time; maybe later with the
join token).

## Updates

A new image and a reboot. Today `kiwi-server build` makes the ISO from the newest CoreOS
release and booting it is the update (netcup: upload, attach, reboot), so someone runs it.
Containers update in the `updates:` window as everywhere.

## Settings

Block `ram:`, in the GUI under *RAM*:

| Setting | Default | |
|---|---|---|
| `ram.copy_to_ram` | `false` | on: the image into RAM too, 3 GiB at least, the medium may go |
| `ram.image_cache` | `true` | off: container images pulled into RAM at every boot |
| `ram.keep_logs` | `false` | off: logs live in RAM, gone at every reboot |

`disk:` holds the partition, `updates:` is the window, backups are the `backup` module's
settings, `coreos.stream`, `kernel_arguments` and `console` apply. `unlock:` does not.

## Later: the phoenix spin

A RAM variant of the phoenix-server image: `[root] transient = true` and `[etc] transient =
true` in `/usr/lib/ostree/prepare-root.conf` (FCOS 44 has ostree 2026.4 with composefs on;
untried), `/var` a tmpfs with the binds above. The image stays read-only on the disk, every
boot starts from it, and `bootc upgrade` in the window updates it with a rollback: every
hoster that takes bootc, no one needed for OS updates. `coreos-ram` stays for provider ISOs
and VMs.

Considered: NixOS with an impermanent root (a third OS, control-plane.md, The image);
Debian live with `toram` (an image we would build and sign ourselves).

## Tried

A local QEMU VM, 11 November 2026: build, boot, `apply` (partition made, stack up),
reboot (same SSH host key, its SSH CA certificate and WireGuard key, a file written to `/etc` gone, stack up 35 to
45 s after boot, SELinux enforcing, no denial), a new image with other settings on the same
disk (same state). Not tried: a VPS, clients, the backup restoring onto a blank partition.
