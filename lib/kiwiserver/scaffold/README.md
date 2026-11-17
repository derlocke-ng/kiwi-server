# Kiwi modules

Modules for [kiwi-server](https://github.com/derlocke-ng/kiwi-server) beyond the built-in ones:
a folder per module in `modules/`, each with its `module.yaml` and templates, in the format of
[kiwi-modules](https://github.com/derlocke-ng/kiwi-modules).

## Use them

```bash
kiwi-server sources ~/kiwi/fleet.yaml add https://github.com/OWNER/REPO
```

That pins the repository to the commit it is at now; nothing of it changes until
`kiwi-server sources ~/kiwi/fleet.yaml update REPO`. Then add a module to a machine like a
built-in one. Its templates and scripts run as root on that machine: add only sources you trust.

## Write one

Copy `modules/hello`, rename the folder and the `name:` in its `module.yaml`. The keys a module
has: [the module reference](https://github.com/derlocke-ng/kiwi-server/blob/main/docs/modules.md).
A name that a built-in module has is refused.

Check that every module renders, as on a machine:

```bash
git clone https://github.com/derlocke-ng/kiwi-modules ../kiwi-modules
python3 tools/check-modules.py
```

A module with required settings gets made-up values for the check in `modules/<name>/check.yaml`:

```yaml
settings:
  fritz_url: https://192.168.2.1
```

The same check runs on every push and pull request (`.github/workflows/check.yml`).
