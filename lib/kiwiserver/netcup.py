"""netcup's Server Control Panel, driven from here: what the first hardware
round did by hand in a web panel (upload an ISO, attach it, boot from it, wait
for the power-off, detach it, read which disk a server really has).

SCP's REST API, OpenAPI at <BASE>/scp-core/api/v1/openapi. Login is Keycloak's
device flow, so no password ever passes through here; the refresh token is kept
in secrets/netcup.token and lives while it is used once in 30 days.
"""
import ipaddress
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import util
from .util import KiwiError

BASE = "https://www.servercontrolpanel.de"
CLIENT_ID = "scp"
AUTH = "/realms/scp/protocol/openid-connect"
API = "/scp-core/api/v1"

# what the installer calls a disk behind each of netcup's drivers: crumble's ISO
# said /dev/vda for a SATA disk and stopped in the live system
DEVICE_BY_DRIVER = {"VIRTIO": "/dev/vda", "VIRTIO_SCSI": "/dev/sda", "SATA": "/dev/sda", "IDE": "/dev/sda"}

POLL_SECONDS = 15
TASK_SECONDS = 2
INSTALL_TIMEOUT = 3600
HTTP_TIMEOUT = 120


class Netcup:
    def __init__(self, token_path, base=BASE):
        self.token_path = token_path
        self.base = base.rstrip("/")
        self.access = None
        self.expires = 0.0
        self._user_id = None

    # ---- HTTP -----------------------------------------------------------------

    def _http(self, method, url, data=None, headers=None):
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except urllib.error.URLError as e:
            raise KiwiError("netcup is not reachable: %s" % e.reason)

    def _form(self, path, **fields):
        status, payload = self._http("POST", self.base + AUTH + path, urllib.parse.urlencode(fields).encode(),
                                     {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        try:
            return status, json.loads(payload.decode() or "{}")
        except ValueError:
            raise KiwiError("netcup's login answered %d with something that is not JSON" % status)

    def api(self, method, path, body=None, query=None, content_type="application/json"):
        url = self.base + API + path + ("?" + urllib.parse.urlencode(query) if query else "")
        headers = {"Authorization": "Bearer " + self._token(), "Accept": "application/json"}
        data = json.dumps(body).encode() if body is not None else None
        if method != "GET":      # netcup answers 400 to a POST without one, body or not
            headers["Content-Type"] = content_type
        status, payload = self._http(method, url, data, headers)
        if status >= 300:
            detail = payload.decode(errors="replace").strip()
            try:
                detail = json.loads(detail).get("message") or detail
            except (ValueError, AttributeError):
                pass
            raise KiwiError("netcup %s %s: %d %s" % (method, path, status, detail[:200]))
        return json.loads(payload.decode()) if payload else None

    # ---- login ----------------------------------------------------------------

    def login(self, show):
        """The device flow: `show` gets the page to open; returns once signed in."""
        status, d = self._form("/auth/device", client_id=CLIENT_ID, scope="offline_access openid")
        if status != 200:
            raise KiwiError("netcup refused the login: %d %s" % (status, d.get("error_description") or d.get("error")))
        show(d["verification_uri_complete"])
        interval = int(d.get("interval") or 5)
        deadline = time.time() + int(d.get("expires_in") or 600)
        while time.time() < deadline:
            time.sleep(interval)
            status, t = self._form("/token", grant_type="urn:ietf:params:oauth:grant-type:device_code",
                                   device_code=d["device_code"], client_id=CLIENT_ID)
            if status == 200:
                self._keep(t)
                return
            if t.get("error") == "authorization_pending":
                continue
            if t.get("error") == "slow_down":
                interval += 5
                continue
            raise KiwiError("netcup login failed: %s" % (t.get("error_description") or t.get("error")))
        raise KiwiError("the netcup login timed out: nobody confirmed it in time")

    def _keep(self, t):
        self.access = t["access_token"]
        self.expires = time.time() + int(t.get("expires_in") or 300)
        os.makedirs(os.path.dirname(self.token_path), mode=0o700, exist_ok=True)
        util.write_bytes(self.token_path, json.dumps({"refresh_token": t["refresh_token"]}).encode(), 0o600)

    def _token(self):
        if self.access and time.time() < self.expires - 20:
            return self.access
        if not os.path.isfile(self.token_path):
            raise KiwiError("not logged in to netcup: kiwi-server vps login <fleet>")
        refresh = json.loads(util.read_bytes(self.token_path).decode())["refresh_token"]
        status, t = self._form("/token", grant_type="refresh_token", refresh_token=refresh, client_id=CLIENT_ID)
        if status != 200:
            raise KiwiError("the netcup login has expired: kiwi-server vps login <fleet>")
        self._keep(t)
        return self.access

    def user_id(self):
        if self._user_id is None:
            status, payload = self._http("GET", self.base + AUTH + "/userinfo",
                                         headers={"Authorization": "Bearer " + self._token(), "Accept": "application/json"})
            info = json.loads(payload.decode() or "{}") if status == 200 else {}
            if not info.get("id"):
                raise KiwiError("netcup did not say which user this is (userinfo answered %d)" % status)
            self._user_id = info["id"]
        return self._user_id

    # ---- servers --------------------------------------------------------------

    def servers(self):
        return self.api("GET", "/servers") or []

    def server(self, sid):
        return self.api("GET", "/servers/%d" % sid, query={"loadServerLiveInfo": "true"})

    def find(self, ref):
        """A server by id, address, name, hostname or nickname."""
        ref = str(ref)
        if ref.isdigit():
            return self.server(int(ref))
        try:
            ipaddress.ip_address(ref)
            rows = self.api("GET", "/servers", query={"ip": ref}) or []
        except ValueError:
            rows = [s for s in self.servers() if ref in (s.get("name"), s.get("hostname"), s.get("nickname"))]
        if len(rows) != 1:
            raise KiwiError("%s names %s netcup server%s (kiwi-server vps list)"
                            % (ref, "no" if not rows else str(len(rows)), "" if len(rows) == 1 else "s"))
        return self.server(rows[0]["id"])

    def disks(self, sid):
        return self.api("GET", "/servers/%d/disks" % sid) or []

    def state(self, sid):
        return (self.server(sid).get("serverLiveInfo") or {}).get("state")

    def wait(self, task, what):
        if not task or "uuid" not in task:
            return
        deadline = time.time() + 900
        while time.time() < deadline:
            t = self.api("GET", "/tasks/%s" % task["uuid"])
            if t["state"] == "FINISHED":
                return
            if t["state"] in ("ERROR", "CANCELED", "ROLLBACK"):
                raise KiwiError("netcup: %s failed: %s" % (what, (t.get("responseError") or {}).get("message") or t.get("message") or t["state"]))
            time.sleep(TASK_SECONDS)
        raise KiwiError("netcup: %s did not finish" % what)

    def power(self, sid, on):
        q = None if on else {"stateOption": "POWEROFF"}
        self.wait(self.api("PATCH", "/servers/%d" % sid, {"state": "ON" if on else "OFF"}, q, "application/merge-patch+json"),
                  "powering %s" % ("on" if on else "off"))

    def boot_order(self, sid, order):
        self.wait(self.api("PATCH", "/servers/%d" % sid, {"bootorder": order}, None, "application/merge-patch+json"), "the boot order")

    # ---- ISOs -----------------------------------------------------------------

    def upload_iso(self, key, path):
        d = self.api("POST", "/users/%s/isos/%s" % (self.user_id(), urllib.parse.quote(key)), query={"multipart": "false"})
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            status, payload = self._http("PUT", d["presignedUrl"], f,
                                         {"Content-Length": str(size), "Content-Type": "application/octet-stream"})
        if status >= 300:
            raise KiwiError("netcup's storage refused the ISO: %d %s" % (status, payload.decode(errors="replace")[:200]))

    def delete_iso(self, key):
        self.api("DELETE", "/users/%s/isos/%s" % (self.user_id(), urllib.parse.quote(key)))

    def attach_iso(self, sid, key):
        self.wait(self.api("POST", "/servers/%d/iso" % sid, {"userIsoName": key, "changeBootDeviceToCdrom": True}), "attaching the ISO")

    def detach_iso(self, sid):
        self.wait(self.api("DELETE", "/servers/%d/iso" % sid), "detaching the ISO")

    def attached_iso(self, sid):
        d = self.api("GET", "/servers/%d/iso" % sid) or {}
        return d.get("iso") if d.get("isoAttached") else None


def device_for(disk):
    return DEVICE_BY_DRIVER.get(disk.get("storageDriver"))


def probe(client, server):
    """What a server is, as data: what the fleet file's disk: should say among it."""
    sid = server["id"]
    live = server.get("serverLiveInfo") or {}
    return {"id": sid, "name": server.get("name"), "hostname": server.get("hostname"), "nickname": server.get("nickname"),
            "state": live.get("state"), "architecture": server.get("architecture"), "uefi": live.get("uefi"),
            "rescue": server.get("rescueSystemActive"), "cpus": live.get("cpuCount"),
            "memory_mib": live.get("maxServerMemoryInMiB"), "bootorder": live.get("bootorder"),
            "addresses": [a["ip"] for a in server.get("ipv4Addresses") or []],
            "disks": [{"name": d["name"], "mib": d.get("capacityInMiB"), "driver": d.get("storageDriver"), "device": device_for(d)}
                      for d in client.disks(sid)],
            "iso": client.attached_iso(sid)}


def boot_live(client, server, iso_path, key, say):
    """A RAM master: its image stays attached and is what it boots, every time. A new image
    replaces the one attached before, which then leaves netcup's store; the disk is not used."""
    sid = server["id"]
    old = client.attached_iso(sid)
    say("uploading %s (%d MB)" % (os.path.basename(iso_path), os.path.getsize(iso_path) >> 20))
    client.upload_iso(key, iso_path)
    if client.state(sid) != "SHUTOFF":
        say("powering off")
        client.power(sid, False)
    client.attach_iso(sid, key)
    client.power(sid, True)
    say("booting the image from RAM")
    if old and old != key:
        try:
            client.delete_iso(old)
        except KiwiError as e:
            say("the image before (%s) stays in netcup's store: %s" % (old, e))


def install(client, server, iso_path, key, say):
    """Upload, attach, boot from it, wait for the installer's power-off, detach,
    the disk first in the boot order again, the ISO out of netcup's store, power on."""
    sid = server["id"]
    order = (server.get("serverLiveInfo") or {}).get("bootorder") or ["HDD", "CDROM", "NETWORK"]
    say("uploading %s (%d MB)" % (os.path.basename(iso_path), os.path.getsize(iso_path) >> 20))
    client.upload_iso(key, iso_path)
    if client.state(sid) != "SHUTOFF":
        say("powering off")
        client.power(sid, False)
    client.attach_iso(sid, key)
    say("booting the installer")
    client.power(sid, True)
    started, ran = time.time(), False
    while time.time() - started < INSTALL_TIMEOUT:
        time.sleep(POLL_SECONDS)
        state = client.state(sid)
        ran = ran or state == "RUNNING"
        if ran and state == "SHUTOFF":
            break
    else:
        raise KiwiError("the installer did not power off within %d minutes; the ISO is still attached, as %s in netcup's store"
                        % (INSTALL_TIMEOUT / 60, key))
    say("installed in %d minutes: detaching the ISO, booting from the disk" % ((time.time() - started) / 60))
    client.detach_iso(sid)
    client.boot_order(sid, ["HDD"] + [b for b in order if b != "HDD"])
    client.delete_iso(key)
    client.power(sid, True)
