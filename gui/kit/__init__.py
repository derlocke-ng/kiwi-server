"""kiwikit — the shared parts of every Kiwi Network desktop app.

This folder is the same in every app that uses it: copied unchanged from
github.com/derlocke-ng/kiwi-template (tools/kit-update.sh), checked by
tools/kit-check.sh. Change it there, never in an app.

It implements docs/ui-rules.md. An app tells the kit who it is with one
`AppInfo` and gets the header bar, the menu, About, Preferences (Appearance,
Window), the remembered window size, the settings file, the worker and the
job strip with the filling kiwi.

Nothing in here imports the app it is copied into.
"""

from .info import AppInfo

KIT_VERSION = "0.1.3"

__all__ = ["KIT_VERSION", "AppInfo"]
