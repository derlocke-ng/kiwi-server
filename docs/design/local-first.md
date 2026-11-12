# Local first (proposal, 12 November 2026)

Asked for by the owner: use local disk instead of the WAN, install home machines from the
network, and let someone start a whole network from a Raspberry Pi, without a Linux desktop.
Three pieces, each useful alone, in this order.

## 1. The cache (the `cache` module)

One node per home keeps what every machine there would fetch again:

- **Container images**: a pull-through mirror per registry (docker.io, ghcr.io, quay.io,
  lscr.io). podman on the other nodes asks it first and the registry itself when it does not
  answer.
- **Debian packages**: a caching proxy for deb.debian.org and security.debian.org. apt on the
  other nodes finds it with a small detect script and goes direct when it is away; the Debian
  installer takes it as its proxy.
- **Install images** (Fedora CoreOS, Debian netinst): what `kiwi-server build` downloads, and
  what netboot serves.

Only machines on the cache's own LAN use it: over the mesh every byte would cross the master.
The fleet knows the LAN addresses, so nothing is set per machine. Nothing breaks when the cache
is off: every client falls back.

Images of our own (the phoenix uCore image, later a controller image) are built locally into
a registry on the same node and installed from it; the upstream registries are only the source
of the base. That is the one idea worth taking from ublue-os/forge (a local registry that
images are built into and machines install from). The rest of forge duplicates what the fleet
has: its own CA, a reverse proxy, Ansible. Its PXE part is a plan, not code.

## 2. Netboot (the `netboot` module)

The roadmap's entry stands: proxy DHCP that answers PXE requests only (no test node serves
DHCP), iPXE over HTTP, each machine's installer chosen by its MAC address (`mac:` on the
host), a one-shot "reinstall" flag the netboot node clears, Wake-on-LAN from the same node.
It boots the kernel and initrd of the same installers the ISOs carry (CoreOS live PXE images
with the host's Ignition, Debian's netboot installer with its preseed), fetched once into the
cache. A machine whose disk died reinstalls itself, unlocks, applies and restores, with nobody
carrying a stick.

Raspberry Pis do not PXE boot like PCs (their firmware netboots from TFTP on its own terms);
a Pi starts from an SD card image.

## 3. The control node

design/control-plane.md already decides it: kiwi-server's core as a module with an API and a
web UI, on a home node, never on the rented master. What this adds:

- **A starter image for a Raspberry Pi** (arm64, Debian): flash it, plug it in, open
  `http://kiwi.local`. The first-run page is the setup wizard the terminal and the GTK app
  already have (`init --questions` / `--answers`), then it builds the master's image, rents
  nothing by itself but walks through the hoster (`vps install`), enrolls, and from there is
  the control node: fleet file, secrets, CA, backups of the fleet, `watch`, the prune timer,
  the Tang rings, the cache and netboot for its LAN.
- **arm64 everywhere it matters**: the targets build x86-64 images only today. The Pi is a
  Debian arm64 node first (role script and stack are architecture-neutral; the container
  images are multi-arch except our own, which need arm64 builds); CoreOS aarch64 for nodes
  later.
- The GTK app and the CLI stay and talk to the same API.

Order inside: the API and web UI as a container on an existing node (papaya) first, since it
needs no new hardware; the Pi image once that works.

## What is not decided

- Whether the cache also mirrors Fedora/uCore OS updates (ostree/bootc content is large and
  signed upstream; a registry mirror for ghcr.io already covers bootc images).
- Where the control node's secrets live at rest on a Pi (an encrypted partition opened by a
  ring cannot be served by itself: design/control-plane.md, Unlock).
