"""Hosters whose servers kiwi-server can look up and install by itself.

netcup is the first. Another one is a class here with the same few things: how to sign in,
how to list the account's servers, and where its token rests in a fleet. The wizard, the
fleet file (`vps: { provider, server }`) and the commands only know these.
"""
import os

from .util import KiwiError


class Provider:
    id = ""             # the word in the fleet file and on the command line
    name = ""           # how people call it

    def token_file(self):
        """Where a fleet keeps this provider's login, below the fleet's directory."""
        return os.path.join("secrets", "%s.token" % self.id)

    def login(self, token_path, show, base=None):
        """Sign in (`show` gets the page to open in a browser); the token goes into token_path.
        Nothing at the provider is changed by this."""
        raise NotImplementedError

    def servers(self, token_path, base=None):
        """[{id, address, label}] of the servers the signed-in account has."""
        raise NotImplementedError


class Netcup(Provider):
    id, name = "netcup", "netcup"

    def _client(self, token_path, base):
        from . import netcup
        return netcup.Netcup(token_path, base or os.environ.get("KIWI_NETCUP_API") or netcup.BASE)   # (another API: tests)

    def login(self, token_path, show, base=None):
        self._client(token_path, base).login(show)

    def servers(self, token_path, base=None):
        client = self._client(token_path, base)
        rows = []
        for s in client.servers():
            d = client.server(s["id"])
            ips = [a["ip"] for a in d.get("ipv4Addresses") or []]
            if ips:
                rows.append({"id": d["id"], "address": ips[0],
                             "label": " · ".join(str(x) for x in (d.get("nickname"), d.get("name"), ips[0]) if x)})
        return rows


PROVIDERS = {p.id: p for p in (Netcup(),)}


def get(name):
    if name not in PROVIDERS:
        raise KiwiError("%r is no provider kiwi-server knows (it knows: %s)" % (name, ", ".join(PROVIDERS)))
    return PROVIDERS[name]
