# A master that lives in RAM

Designed and prototyped 11 November 2026 (`target: coreos-ram`). The master boots from its
image every time, keeps everything in RAM and touches no disk. Home sends it what makes it
*this* master after every boot.

## The base: CoreOS live

The Fedora CoreOS live ISO is already that: Ignition runs at every boot, the root is a
read-only image, everything written goes to RAM. It has systemd, podman and quadlets, so
kiwi-stack and the role script run unchanged, and it is phoenix-server's lineage. Alpine
diskless is smaller but OpenRC, and every quadlet and host unit of kiwi-stack is systemd.

## The image

`kiwi-server build`: the stock live ISO with the host's live Ignition (`coreos-installer
iso customize --live-ignition`): admin user and keys, hostname, network, and the machine's
**SSH host key**, signed by the fleet's SSH CA when there is one. No role. On netcup the ISO
stays attached and the machine boots from CD-ROM every time.

The host key is the image's one secret and the "passthrough key": it lets home know it is
talking to this master. It is made once per host in `secrets/ram/<host>/` and pinned in the
fleet's `known_hosts`; a new one (delete it, build again) revokes every older image.

## Every boot: home pushes

The roadmap's objection was that the state needs encryption on the hoster's storage, or
someone at boot. The master keeps nothing, and "someone" is a machine at home:

1. The master boots blank: SSH only, no WireGuard, nodes waiting.
2. `kiwi-server watch <fleet>` at home looks at it every minute (`watch --timer on`: a user
   timer; later the control node). It reaches the master at its public address, checks the
   host key, finds no role and runs `apply`. That goes out from home, so NAT is no matter.
3. `apply` puts back what the fleet keeps of the master: the WireGuard identity (as since
   2.38) and the Let's Encrypt certificates and ACME account, so a boot never asks for new
   ones. Then it sends the role and the stack starts.
4. The `backup` module restores the rest over the mesh (`restore: auto`).

While the master runs, `watch` refreshes the fleet's copies hourly, so peers enrolled and
certificates renewed survive a reboot. A token on the medium does not work here: it is used up
at the first boot, and a blank master could only pull from something public.

**Security.** At the hoster there is the image and nothing else. A stolen image alone gets
nothing: home pushes only to the master's own address. Only someone with the image *and* that
address could take the push, which is the hoster, who can read RAM anyway.

**Costs.** After an unplanned reboot the master is down until home looks: a minute with the
timer on, longer when the computer running it is off. Container images are pulled into RAM at
every boot (about 430 MB).

## Measured

In a VM with the master stack (wg-easy, Pi-hole, nginx) and 2 GiB: about 950 MB used, the
images in RAM included, and the stack up 60 to 80 seconds after boot with the image read from
the medium. `coreos.liveiso.fromram` would copy the image into RAM too, about 900 MB more,
and needs 3 GiB: with 2 GiB the initramfs runs out of space and stops in emergency mode. So it
is `ram.copy_to_ram`, off by default.

## Settings

| Setting | Default | |
|---|---|---|
| `ram.copy_to_ram` | `false` | on: the image into RAM too, 3 GiB at least, the medium may go |

`updates:` is the containers' window as everywhere; an OS update is a new image, booted.
Backups are the `backup` module's settings. `coreos.stream`, `kernel_arguments` and
`console` apply. `disk:` and `unlock:` do not.

## Later

- **The phoenix spin**: the same image with the master's container images inside, so nothing
  is pulled into RAM at boot, and `bootc`'s updates with a rollback for machines that boot it
  from a disk (`[root] transient = true`, `[etc] transient = true` in ostree's
  `prepare-root.conf`; untried).
- **The control node** runs `watch`, so the push does not depend on a desktop being on.
- **netcup** swapping the ISO: `vps` uploads a new image and reboots into it.

Considered: a partition with the state on the master's disk (no one needed at an unplanned
reboot, but state at rest; the owner chose RAM only); a blob sealed to a Tang ring (the hoster
has the blob and reaches the ring from the master's address); NixOS (a third OS); Debian live
(an image we would build and sign ourselves).

## Tried

A local QEMU VM with no disk, 11 November 2026, `tests/drills/ram-master-vm.sh`: build,
boot, `watch` sends the role, reboot, `watch` sends it again; the same host key, certificate
and WireGuard key, `/etc` fresh. Not tried: a VPS, clients, a public site's certificates.
