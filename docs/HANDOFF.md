# Handoff to a local session

For a Claude Code session on a machine that can reach the test hardware.
Read this, then `docs/testing-hardware.md` (the runbook),
`docs/ROADMAP.md` (what is done, what is wrong, in which order) and
`docs/design/control-plane.md` (where it is going). The decisions below were
made with the owner; do not reopen them without a reason.

## State

- kiwi-server 2.6.0 (the split: kiwimodules package, `tools/split-sync.sh`)
  is merged and released; kiwi-modules 0.1.0 and kiwi-stack 0.1.0 are
  tagged and released. kiwi-server is still where both are developed: after a change to `lib/kiwimodules`,
  `modules/`, `roles/*/role.yaml`, `roles/common/lib.sh` or `kiwi-stack`, run
  `tools/split-sync.sh`, commit in the two checkouts, and finish with
  `tools/split-sync.sh --check`. Releases are cut by `release.yml` (in all
  three repositories) when a push to main carries a new `VERSION` in
  `kiwi.manifest`; the cloud session cannot push tags itself.
- **The first machine has booted** (30 October 2026): sorbet, the test
  master's VPS, installed from bare Debian media and ran the bare role;
  its first boot found problem 38 (apt before DNS). The uCore media for
  the mini PC failed to boot from a stick written with Ventoy or Rufus
  (`Failed to isolate default target` in the live ISO) and installed
  cleanly in a QEMU VM the same hour: write sticks raw (runbook, step 4).
  The master `apply`, `status`, `enroll`, a CoreOS first boot and every
  stack are still untested on hardware. A step that fails on hardware is a finding for
  `docs/ROADMAP.md`, not something to work around.

## Decisions to keep

- One master per network. Address plan 10.8.0.0/16: 10.8.0.0/24 master and
  nodes, .1 guests, .2 pentest, .3 devices, .4 routers.
- Names are `service.hostname.tld`, the TLD chosen by the owner (`.home`
  when unset). Nodes' Pi-hole forwards to the master's, which forwards to
  Quad9. The master's Pi-hole listens on 8081 inside the WireGuard server's
  namespace, nginx owns 80/443.
- Rootful podman with quadlets now, rootless maybe later. Full disk
  encryption everywhere with Tang/Clevis (nested Shamir), no TPM; the
  unlock is the rings and the netfilter relay of
  `docs/design/control-plane.md` (Unlock), which replaced the earlier
  reverse-tunnel idea. Not built yet, so test machines run without LUKS
  (the VM excepted, to test the preseed).
- One bootc image for every CoreOS-lineage machine, built in the
  phoenix-server repository (empty today; phoenix-desktop and phoenix-docs
  follow, phoenix-main goes); the role arrives at first boot. See
  `docs/design/control-plane.md`, The image. Masters are named after fruit
  dishes, nodes after fruits (Names).
- Control plane: one full admin for all nodes plus admins for their own
  nodes; both ISO paths (generic ISO with a join token as the default,
  per-host ISO with embedded secrets for offline); FastAPI, Jinja, HTMX;
  the GTK app stays and becomes an API client.
- kiwi-game becomes a stand-alone module that manages game servers in
  podman (or docker) and stops idle ones, by itself or by hand in the UI.

## Handling secrets

The owner's original upload held real secrets (CA key, host keys,
WireGuard configs, `.env`). Never copy a secret value into the repo, a
commit message or a log. `output/`, `secrets/` and `*.iso` stay ignored.
Do not put model names in repo files. The seed (`secrets/seed`) and the
CA are the fleet: back them up, never commit them.

## Order of work

1. **Operator machine**: install, `kiwi-server doctor`, a fleet file with
   the master on the test VPS (Debian 13 or Ubuntu 24.04, podman 4.4 or
   newer with catatonit, root over SSH), on the test network below.
2. **Master on the VPS from bare media**: `build --bare`, boot the ISO
   through the provider's panel (it carries no stack and no secret of it),
   then `apply … --ssh core@<address>` and runbook step 2's checks. Expect
   real findings here: the preseed on a VPS, image pulls, the Pi-hole port,
   the Mullvad exit, `kiwi-stack verify`. Fix in this tree, sync, re-apply.
3. **A node on the mini PC** (a Debian install by hand, or the ISO), then
   `enroll` to fetch its WireGuard config and `apply`.
4. **The bridged VM** for the unattended ISO install path: build the ISO,
   boot it, read `/var/lib/kiwi-server/role.failed` if it stops.
5. Re-apply, remove, switch runtime, and the dead-node drill from the
   runbook; record results in the README status section.
6. Only then the ARM boards.

## The test network (answered by the owner, October 2026)

The test round runs beside the owner's live v1 network, which must not
notice it.

- **The live network is off limits.** The first VPS is the live v1 master
  (mesh 10.8.0.0/16, TLD `.kiwi`) and an old kiwi-node serves DHCP on the
  owner's LAN. Never apply to, enroll against or restart anything of it.
- **The second VPS is the test master**, for development and these
  hardware rounds. The standby master and a Tang server come later, on
  other machines.
- **Mesh 10.9.0.0/16, TLD `.home`**, so a device in both meshes can tell
  them apart: 10.9.0.0/24 master and nodes, .1 guests, .2 pentest,
  .3 devices, .4 routers. The fleet file sets every setting that carries
  the subnet (`mesh_subnet`, the vpn-server's `server_ip`, `wg_subnet`,
  `wg_default_address`, `groups`, tor's `socks_policy_accept`); see
  ROADMAP.md, problem 32.
- **Exit through Mullvad in Switzerland.**
- **LAN machines get static addresses.** No test node serves DHCP on the
  owner's LAN (no dhcp-relay, no Pi-hole DHCP).
- **Masters are named after fruit dishes, nodes after fruits**, the rule
  that is now general (design/control-plane.md, Names).

## Test machines (October 2026)

The fleet file is `~/kiwi/fleet.yaml` on the operator machine, outside git;
its addresses and ssh aliases stay there.

- **sorbet**, the master: the second VPS (2 CPUs, 1.9 GiB, 20 GB,
  `/dev/vda`), **reinstalled from bare media** (`build --bare`) as Debian
  trixie with podman, the master role applied over SSH afterwards; the
  provider boots a custom ISO, and that ISO holds no secret of the stack.
  The Ubuntu 22.04 it came with (podman 3.4, docker 29, a sudo that asks
  for a password) is gone with the reinstall, and with it the `runtime:
  docker` variant.
- **papaya**, gateway and cloud on one node: the mini PC, no OS yet, a big
  NVMe for the system and a 1 TB SATA SSD for the data at `/mnt/data`.
  The data disk is formatted and mounted by hand before the first apply
  (problem 36). Modules: the gateway preset without dhcp-relay, plus
  cloud and vault. uCore from bare media too (`build --bare`, so the ISO
  needs no WireGuard config yet), `enroll` and `apply` once sorbet is up.
- **lychee**, the ISO path: a bridged VM, Debian with a LUKS-encrypted
  disk (`debian.encrypt`, the passphrase typed at the console on every
  boot until Tang exists), the minimal stack plus Portainer.
- **crumble**, the master of a **second test network** (`~/kiwi2`: its own
  seed and CA, mesh 10.10.0.0/16, TLD `.internal`), on the owner's third
  VPS, plain Fedora CoreOS from bare media: the master stack's first boot
  on the CoreOS lineage, and later the other side of federation. One
  master per network is a decision, so a second master is a second fleet
  file. It is a master and not a node because a node publishes its
  services on the host, which on a VPS is the internet (problem 37). It is
  a **direct-exit master** (`modules:` without vpn-client, `mullvad_socks`
  off; design/control-plane.md, Exits): its clients leave through the VPS's
  own uplink, so it needs no Mullvad device, and it is the first test of
  that mode.

## ARM boards (checked against the tree, not on hardware)

- The Debian ISO path is amd64 only: `lib/kiwiserver/iso.py` resolves the
  amd64 netinst, `targets/debian.py` installs `linux-image-amd64`.
- Whether each pinned image has an arm64 variant is unchecked. The
  Nextcloud AIO images, gluetun, wg-easy, Pi-hole, nginx and Portainer
  publish multi-arch images as far as known; the sftp image does not by
  default (the module help names a fork). Check `skopeo inspect
  --raw` on each image in `KS_STACK_IMAGES` of a rendered stack.
- Whether uCore and CoreOS run the same stacks on aarch64 is untested.
  For ARM boards the first step is a hand-installed Debian arm64 plus
  `kiwi-stack apply DIR` from a bundle rendered elsewhere, not an ISO.
- CI downloads the x86-64 Butane build; an ARM operator machine needs its
  own.

## Checks before every push

```
python3 -W error::ResourceWarning -m unittest discover -s tests
bash tests/cli.sh
shellcheck -S warning roles/common/lib.sh roles/common/kiwi-stack roles/*/apply.sh install.sh bin/kiwi-server tests/cli.sh tests/check-version.sh tools/split-sync.sh
bash tests/check-version.sh
bash tools/split-sync.sh --check
```

`tests/golden/` is the contract with machines already applied: change it
only on purpose (`UPDATE_GOLDEN=1`) and say why in the commit.
