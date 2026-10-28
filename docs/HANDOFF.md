# Handoff to a local session

For a Claude Code session on a machine that can reach the test hardware.
Read this, then `docs/testing-hardware.md` (the runbook),
`docs/ROADMAP.md` (what is done, what is wrong, in which order) and
`docs/design/control-plane.md` (where it is going). The decisions below were
made with the owner; do not reopen them without a reason.

## State

- kiwi-server 2.6.0 (the split: kiwimodules package, `tools/split-sync.sh`)
  is on branch `claude/magical-volta-jyjhn9` until the owner merges it;
  2.5.0 is the last release.
- kiwi-modules and kiwi-stack are populated (0.1.0, untagged). kiwi-server is
  still where both are developed: after a change to `lib/kiwimodules`,
  `modules/`, `roles/*/role.yaml`, `roles/common/lib.sh` or `kiwi-stack`, run
  `tools/split-sync.sh`, commit in the two checkouts, and finish with
  `tools/split-sync.sh --check`. Releases are cut by `release.yml` (in all
  three repositories) when a push to main carries a new `VERSION` in
  `kiwi.manifest`; the cloud session cannot push tags itself.
- **Nothing has booted on hardware yet.** Every stack is the live v1 setup
  rendered from modules and checked file by file, but first boot, ISO
  install, `apply`, `status` and `enroll` against a live master are only
  tested against fakes. A step that fails on hardware is a finding for
  `docs/ROADMAP.md`, not something to work around.

## Decisions to keep

- One master per network. Address plan 10.8.0.0/16: 10.8.0.0/24 master and
  nodes, .1 guests, .2 pentest, .3 devices, .4 routers.
- Names are `service.hostname.tld`, the TLD chosen by the owner (`.home`
  when unset). Nodes' Pi-hole forwards to the master's, which forwards to
  Quad9. The master's Pi-hole listens on 8081 inside the WireGuard server's
  namespace, nginx owns 80/443.
- Rootful podman with quadlets now, rootless maybe later. Full disk
  encryption everywhere with Tang/Clevis (nested Shamir), no TPM,
  reverse-tunnel unlock; this is not built yet, so test machines run
  without LUKS.
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
   the master on the first VPS (Debian 13 or Ubuntu 24.04, podman 4.4 or
   newer with catatonit, root over SSH).
2. **Master on the VPS** with `apply` (runbook step 2). Expect real
   findings here: image pulls, the Pi-hole port, the Mullvad exit,
   `kiwi-stack verify`. Fix in this tree, sync, re-apply.
3. **A node on the mini PC** (a Debian install by hand, or the ISO), then
   `enroll` to fetch its WireGuard config and `apply`.
4. **The bridged VM** for the unattended ISO install path: build the ISO,
   boot it, read `/var/lib/kiwi-server/role.failed` if it stops.
5. Re-apply, remove, switch runtime, and the dead-node drill from the
   runbook; record results in the README status section.
6. Only then the second VPS and the ARM boards.

## Open questions for the owner

- Is the second VPS a standby master, a node, or a Tang server? The plan
  says standby master later; a Tang server is needed before FDE exists.
- Which provider and exit country for the master's VPN client?
- The TLD for the test network (`.home` unless told otherwise).
- Do the LAN machines get static addresses or DHCP from the gateway
  node's Pi-hole?

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
