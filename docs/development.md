# Development

## Tests

CI runs on every push:

- every rendered script is shellchecked
- every Butane config is validated with `butane --strict`
- every preset's compose file is checked with `docker compose config`
- the Debian ISO rebuild runs against a mock netinst
- `apply`, `status` and `enroll` run against fake SSH runners and a fake
  wg-easy
- the version agrees in `kiwi.manifest`, `lib/kiwiserver/__init__.py` and
  the top of the CHANGELOG (`tests/check-version.sh`)

Locally:

```bash
python3 -m unittest discover -s tests -v
bash tests/cli.sh
```

What the suite cannot prove is a machine that boots, installs and runs a
stack. [Testing on real hardware](testing-hardware.md) is the runbook for
that; the drills are in
[tests/drills/](https://github.com/derlocke-ng/kiwi-server/tree/main/tests/drills).

## kiwi-modules and kiwi-stack

The renderer and the machine side are repositories of their own:

- [kiwi-modules](https://github.com/derlocke-ng/kiwi-modules) renders one
  machine's stack from a machine file without a fleet.
- [kiwi-stack](https://github.com/derlocke-ng/kiwi-stack) applies such a
  bundle (`sudo kiwi-stack apply DIR`).

This tree carries both as vendored copies pinned by release in `VENDORED`
(`tools/vendor.sh`, `--check` in CI). The stacks they produce are this
tree's, byte for byte (`tests/golden`). A change to them is made there,
released by tag, and vendored here with `tools/vendor.sh`.

## Releases

Release notes:
[CHANGELOG.md](https://github.com/derlocke-ng/kiwi-server/blob/main/CHANGELOG.md).
