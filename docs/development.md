# Development

This page is for people who change kiwi-server: how the tree is organised,
which tests run where, how kiwi-modules and kiwi-stack are carried, and how a
release is made. The notes the project keeps for its own work are listed at
the end.

## The tree

| Folder | What it holds |
|---|---|
| `bin/`, `lib/kiwiserver/` | the command-line tool: fleet file, targets, ISOs, SSH, secrets, backups |
| `gui/` | the GTK app, which runs the command-line tool with `--porcelain` |
| `lib/kiwimodules/`, `modules/` | the renderer and the modules, a vendored copy of kiwi-modules |
| `roles/` | the roles; `roles/common/lib.sh` and `roles/common/kiwi-stack` are a vendored copy of kiwi-stack |
| `container/` | the Dockerfile of the ISO toolchain image |
| `examples/` | the commented example fleet |
| `tests/` | unit tests, golden stacks, the CLI smoke test, and drills for real machines |
| `tools/` | `vendor.sh` and a preview helper for the app |

## kiwi-modules and kiwi-stack

The renderer and the machine side are repositories of their own:
[kiwi-modules](https://kiwi-network.eu/docs/server/modules/) renders one
machine's stack from a machine file without a fleet, and
[kiwi-stack](https://kiwi-network.eu/docs/server/single/) applies such a
bundle on the machine (`sudo kiwi-stack apply DIR`). Both are developed and
released there.

This tree carries both as vendored copies, pinned by release tag in
[VENDORED](https://github.com/derlocke-ng/kiwi-server/blob/main/VENDORED).
v2.57.0 carries kiwi-modules v0.28.0 and kiwi-stack v0.7.0. Copies are used
instead of submodules or pip, because those do not survive the ostree desktops
that kiwi-updater installs onto.

```bash
tools/vendor.sh                                       # copy in the pinned releases
tools/vendor.sh --check                               # report every difference (CI runs this)
tools/vendor.sh kiwi-modules=v0.28.0 kiwi-stack=v0.7.0   # pin new releases and copy them in
```

To change a module, the renderer or `lib.sh`, change it in kiwi-modules or
kiwi-stack, release it there, and then pin the new release here. The stacks
they produce are this tree's, byte for byte; `tests/golden/` checks that.

## Tests

CI (`.github/workflows/ci.yml`) runs on every push and pull request, on
GitHub's `ubuntu-latest`:

- `shellcheck -S warning` on the launcher, the installer, the completion, the
  library, `kiwi-stack` and every role's `apply.sh`;
- `py_compile` on every Python file;
- `tests/check-version.sh`: the version in `kiwi.manifest`,
  `lib/kiwiserver/__init__.py` and the top of `CHANGELOG.md` agree, and the
  vendored kiwi-modules is the release `VENDORED` names;
- `tools/vendor.sh --check`;
- the unit tests: every rendered script is shellchecked, every Butane config
  is validated with `butane --strict`, every preset's compose file is checked
  with `docker compose config`, the Debian ISO is rebuilt from a mock netinst
  image, and `apply`, `status` and `enroll` run against fake SSH and a fake
  wg-easy;
- `tests/cli.sh`: installs into a temporary prefix and renders the example
  fleet from there.

Run the same before you push:

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests
bash tests/cli.sh
bash tests/check-version.sh
bash tools/vendor.sh --check
```

Some unit tests need `ssh-keygen` and fail without it. Tests that need
`butane`, `xorriso`, `curl`, `shellcheck` or `docker compose` are skipped when
the tool is missing.

`tests/golden/` holds the rendered stacks of four reference hosts. They are
the contract with machines that already run: change them only on purpose
(`UPDATE_GOLDEN=1`) and say why in the commit.

## Tests on real machines

The unit tests cannot boot a machine. The first hardware round, in October
and November 2026, installed Debian, uCore and Fedora CoreOS from these ISOs
on two VPSes, a mini PC and a VM, and ran the master and node stacks on them.
[Testing on real hardware](testing-hardware.md) is the runbook for such a
round.

The
[drills](https://github.com/derlocke-ng/kiwi-server/blob/main/tests/drills/README.md)
break test machines on purpose (a power cut, a stopped master, a hung backup
server) and measure what happens. They refuse every machine that is not listed
in a `drills.yaml` next to the fleet file.

## Releases

A release is the tag `v<VERSION>` for the `VERSION` in `kiwi.manifest`; kiwi
installs the newest such tag. `.github/workflows/release.yml` runs on every
push to `main`. When the tag for the manifest's version does not exist yet, it
checks that the version agrees everywhere and is newer than the last release,
creates the tag, and publishes a GitHub release with the matching section of
[CHANGELOG.md](https://github.com/derlocke-ng/kiwi-server/blob/main/CHANGELOG.md).
A push that leaves `VERSION` alone releases nothing.

To release: bump `VERSION` in `kiwi.manifest` and `lib/kiwiserver/__init__.py`,
add a `## X.Y.Z` section at the top of `CHANGELOG.md`, and merge to `main`.
kiwi-network.eu shows this `docs/` folder at the latest release tag, so a
change to the docs reaches the site with the next release.

## Project notes

These pages are the project's own working notes. They record decisions,
findings and plans as they were written, and some of their details are older
than the code.

| Page | What it is |
|---|---|
| [Roadmap and known issues](ROADMAP.md) | what is done, the problems found, and the agreed order of work |
| [Handoff](HANDOFF.md) | the state and the decisions, for a session that works on the test machines |
| [Review of 2.1.0](REVIEW-2.1.0.md) | the findings of the review of release 2.1.0 |
| [The control plane](design/control-plane.md) | the design of kiwi-server as a service on a node; designed, not built |
| [Federation](design/federation.md) | the design of linked networks |
| [A master in RAM](design/ram-master.md) | the design of the `coreos-ram` target |
| [Local first](design/local-first.md) | the plan for the LAN cache, netboot and a control node |
