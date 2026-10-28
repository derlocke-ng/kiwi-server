"""kiwi-modules — render one machine's stack into a bundle directory.

    python3 -m kiwimodules render machine.yaml -o DIR      # files, host-units/, quadlets/, stack.sh
    python3 -m kiwimodules modules [-v]                    # the modules and their settings
    python3 -m kiwimodules profiles                        # master, node and its presets

Then, on the machine:  sudo kiwi-stack apply DIR
"""
import argparse
import os
import sys

from . import VERSION, machine as machinemod, manifest, modules as modmod, profiles as profmod, resolver
from .util import KiwiError, write_bytes, write_text


def write_bundle(out_dir, m, profile, bundle):
    if os.path.exists(out_dir) and os.listdir(out_dir):
        raise KiwiError("%s exists and is not empty" % out_dir)
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    for rel, (content, mode) in bundle.files.items():
        p = os.path.join(out_dir, rel)
        (write_bytes if isinstance(content, bytes) else write_text)(p, content, mode)
    for name, text in bundle.units.items():
        write_text(os.path.join(out_dir, "host-units", name), text)
    for name, text in bundle.quadlets.items():
        write_text(os.path.join(out_dir, "quadlets", name), text)
    write_text(os.path.join(out_dir, "stack.sh"), manifest.stack_manifest(m, profile, "kiwi-modules %s" % VERSION, bundle), 0o644)


def cmd_render(args):
    m = machinemod.load(args.machine)
    profiles = profmod.discover()
    if m.role not in profiles:
        raise KiwiError("%s: unknown profile %r (have: %s)" % (m.name, m.role, ", ".join(sorted(profiles))))
    profile = profiles[m.role]
    if not profile.stack:
        raise KiwiError("%s: profile %s has no stack to render" % (m.name, m.role))
    ms = modmod.discover()
    resolver.resolve_settings(profile, m, ms)
    spec = resolver.stack_spec(m, profile, m.dns_records, master_ip=m.master_ip, ca_cert=m.ca_cert)
    bundle = modmod.Renderer(ms).render(spec)
    write_bundle(args.output, m, profile, bundle)
    print("%s: %s -> %s (%s)" % (m.name, ", ".join(bundle.modules), args.output, bundle.runtime))
    for w in m.warnings:
        print("warning: %s" % w, file=sys.stderr)
    for url in bundle.urls:
        print("  %s" % url)
    print("on the machine: sudo kiwi-stack apply %s" % args.output)


def cmd_modules(args):
    ms = modmod.discover()
    for mod in ms.modules.values():
        print("%-14s %-12s %s" % (mod.name, "/".join(mod.node_types), mod.description))
        if args.verbose:
            for s in mod.settings:
                print("               %-24s %-7s default=%r%s" % (s.key, s.type, s.default, " (required)" if s.required else ""))


def cmd_profiles(_args):
    for p in profmod.discover().values():
        print("%-12s %s" % (p.name, p.description or p.title))
        for name, pr in p.presets.items():
            print("               preset %-10s %s" % (name, ", ".join(pr["modules"])))


def main(argv=None):
    p = argparse.ArgumentParser(prog="kiwi-modules", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version="kiwi-modules %s" % VERSION)
    sp = p.add_subparsers(dest="cmd")
    r = sp.add_parser("render", help="one machine's stack into a bundle directory"); r.set_defaults(fn=cmd_render)
    r.add_argument("machine"); r.add_argument("-o", "--output", required=True)
    mm = sp.add_parser("modules", help="the modules"); mm.set_defaults(fn=cmd_modules)
    mm.add_argument("-v", "--verbose", action="store_true")
    sp.add_parser("profiles", help="the profiles and their presets").set_defaults(fn=cmd_profiles)
    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help()
        return 0
    try:
        args.fn(args)
        sys.stdout.flush()
    except KiwiError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 0


if __name__ == "__main__":
    sys.exit(main())
