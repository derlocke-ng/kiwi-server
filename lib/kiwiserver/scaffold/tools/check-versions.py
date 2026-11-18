#!/usr/bin/env python3
"""A change to a module bumps its version (module.yaml) and the source's (VERSION): what a fleet
pins is a version tag, and `kiwi-server sources … update` says which module went from what to what.

    python3 tools/check-versions.py BASE      # BASE: the commit or branch the change is against
"""
import os
import subprocess
import sys

import yaml

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def at(ref, path):
    r = subprocess.run(["git", "-C", HERE, "show", "%s:%s" % (ref, path)], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def main(base):
    changed = subprocess.run(["git", "-C", HERE, "diff", "--name-only", base + "...HEAD"], capture_output=True,
                             text=True, check=True).stdout.split()
    modules = sorted({p.split("/")[1] for p in changed if p.startswith("modules/") and p.count("/") >= 2})
    bad = []
    for m in modules:
        path = "modules/%s/module.yaml" % m
        now = yaml.safe_load(open(os.path.join(HERE, path))) if os.path.isfile(os.path.join(HERE, path)) else None
        was = at(base, path)
        if now is None or was is None:
            continue                    # a new module, or one that left: nothing to compare
        if str((now.get("module") or {}).get("version")) == str((yaml.safe_load(was).get("module") or {}).get("version")):
            bad.append("%s changed and its version (module.yaml) did not" % m)
    if modules and (at(base, "VERSION") or "").strip() == open(os.path.join(HERE, "VERSION")).read().strip():
        bad.append("modules changed and VERSION did not (the source's version, tagged when merged)")
    for b in bad:
        print("FAIL  " + b)
    print("versions: %s" % ("ok" if not bad else "%d to bump" % len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "origin/main"))
