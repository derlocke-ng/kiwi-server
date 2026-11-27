"""The public pages in a container of their own: the website, the status page for the Internet and
the public registry, served from a folder the control part fills (control.Control.write_public)
and that is mounted here read-only. No fleet, no secret store, no key, no SSH: a fault in a page
that strangers can reach finds nothing of the network behind it.

    kiwi-server serve-public FOLDER [--listen 0.0.0.0:8741]

FOLDER holds health.json (health.for_strangers), info.json ({domain}) and ca.pem (the CA's
certificate, public). The website comes from its pinned repository as on the control part
(KIWI_SITE_*), fetched into a folder of this container's own.
"""
import json
import os
import tempfile

from . import util


class PublicControl:
    """What the public routes of web.make_app ask of a control part, from FOLDER and nothing else."""
    public_only = True

    def __init__(self, folder):
        self.folder = folder
        self.run = tempfile.mkdtemp(prefix="kiwi-public-")        # the website's checkout: this container's own
        self.dir = self.run
        self.fleet_dir = self.run
        self.fleet_path = os.path.join(self.run, "fleet.yaml")
        self.jobs_dir = self.run

    def _json(self, name):
        try:
            with open(os.path.join(self.folder, name), encoding="utf-8") as fh:
                got = json.load(fh)
            return got if isinstance(got, dict) else {}
        except (OSError, ValueError):
            return {}

    def health(self):
        return self._json("health.json")

    def fleet_info(self, fresh=False):
        return {"domain": self._json("info.json").get("domain") or "", "hosts": []}

    def status(self):
        return {}

    def ca_pem(self):
        try:
            return util.read_text(os.path.join(self.folder, "ca.pem"))
        except OSError:
            return ""

    def locked(self):
        return False

    def damaged(self):
        # nothing here can be: there is no fleet in this container. /healthz asks (it answered
        # with an error 500 until 2.318.0, found when the container got a health check)
        return []

    def __getattr__(self, name):
        # anything else of a control part is not here: a route that asks for it is not a public one
        raise AttributeError("not on the public pages: %s" % name)
