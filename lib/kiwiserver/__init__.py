"""kiwiserver — turn a small fleet.yaml into first-boot role scripts, Ignition
or preseed configs and unattended install ISOs for Kiwi Network machines.

Targets: Fedora CoreOS, uCore (CoreOS rebased onto a ublue image), Debian stable.
Roles:   bare, node-cloud (the kiwi-cloud stack), master — plus any directory
         dropped into roles/ that follows the same two-file convention.
"""
VERSION = "2.7.0"

# the renderer, the quadlet converter and the settings schema live in kiwimodules
# (the kiwi-modules package, vendored under lib/); the old names keep working
import sys as _sys  # noqa: E402

import kiwimodules.modules as _modules, kiwimodules.quadlet as _quadlet, kiwimodules.schema as _schema  # noqa: E402
_sys.modules[__name__ + ".modules"] = _modules
_sys.modules[__name__ + ".quadlet"] = _quadlet
_sys.modules[__name__ + ".schema"] = _schema
modules, quadlet, schema = _modules, _quadlet, _schema
