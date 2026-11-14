"""`kiwi-server logins`: what a person types to get into a machine's services.

A module says in its module.yaml what there is to sign in to (`logins:`, kiwimodules.schema.Login):
a label, the site, the user name and the setting that holds the password. Here a machine's
own values go in: the address, the user name, and the password as the fleet has it, written
in the fleet file or generated from the seed. Nothing is asked of the machine, so a password
that was changed inside a service is not known here.

The passwords are the answer of this command and of the control page's /api/hosts/<name>/logins,
and of nothing else: never a log, the audit or an error message.
"""
from . import modules as modmod, roles as rolesmod
from .util import KiwiError

COLUMNS = ("SERVICE", "ADDRESS", "USER NAME", "PASSWORD")


def for_host(host, role, ms):
    """The logins of one machine whose settings are resolved (roles.resolve_settings), in the
    order of its modules: [{module, label, address, user, has_password, password, note}].
    Only what is on: a module the machine does not run, or a part of one that is switched
    off, is not in it. The address is that of a site (the names `list` gives as `sites`)
    with the login's path, or empty when nothing serves it."""
    if not role.stack:
        return []
    found = modmod.Renderer(ms).logins(rolesmod.stack_spec(host, role))
    return [dict(x, has_password=bool(x["password"])) for x in found]


def public(logins):
    """The same without the passwords: what `list --porcelain` gives and a page may keep."""
    return [{k: v for k, v in x.items() if k != "password"} for x in logins]


def listed(host, roles, ms):
    """A machine's logins without the passwords, for `list --porcelain`; none when they
    cannot be worked out."""
    try:
        return public(for_host(host, roles[host.role], ms))
    except (KiwiError, KeyError):
        return []


def text(rows):
    """The machines' logins as a short aligned list: a line per service, its note below.
    `rows` is [{name, hostname, logins}]."""
    out = []
    for row in rows:
        if not row.get("logins"):
            continue
        cells = [COLUMNS] + [(x["label"], x["address"] or "-", x["user"] or "-", x["password"] or "-") for x in row["logins"]]
        width = [max(len(c[i]) for c in cells) for i in range(len(COLUMNS) - 1)]
        if out:
            out.append("")
        out.append(row["name"])
        for cell, x in zip(cells, [None] + row["logins"]):
            out.append(("  " + "  ".join(c.ljust(w) for c, w in zip(cell, width)) + "  " + cell[-1]).rstrip())
            if x and x["note"]:
                out.append("      " + x["note"])
    return "\n".join(out)
