"""What kiwi-desktop knows without GTK: the agent's status file, a join command, the agent's
commands as root. Kept apart from the window so that it can be tested anywhere (tests/test_desktop.py).

The agent (kiwi-server's clientagent.py) writes /run/kiwi-client/status every minute, readable by
everyone and without a secret in it: one fact a line, "key value".
"""
import base64
import os
import re
import ssl
import subprocess
import tempfile
import time
import urllib.request

STATUS = os.environ.get("KIWI_CLIENT_STATUS", "/run/kiwi-client/status")
AGENT = "/usr/local/sbin/kiwi-client-agent"
JOIN = re.compile(r"echo\s+([A-Za-z0-9+/=]+)\s*\|\s*base64 -d.*?(https://[^\s);\"']+/api/client/join/[A-Za-z0-9_-]{16,64})")


def read_status(path=STATUS):
    """The agent's last word: {} when there is no agent (not joined) or nothing yet."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return {}
    out = {"vpn": []}
    for line in text.splitlines():
        key, _sp, value = line.partition(" ")
        value = value.strip()
        if key == "vpn":
            link, _sp, addr = value.partition(" ")
            out["vpn"].append({"link": link, "address": addr})
        elif key in ("asked", "ok", "uptime", "crypt", "clevis") and value.isdigit():
            out[key] = int(value)
        elif key:
            out[key] = value
    return out


def ago(seconds):
    """'just now', '5 minutes ago', '2 hours ago', '3 days ago'."""
    seconds = max(0, int(seconds))
    if seconds < 90:
        return "just now"
    for size, word in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            n = seconds // size
            return "%d %s%s ago" % (n, word, "" if n == 1 else "s")
    return "just now"


def reported(st, now=None):
    """(level, words) of the last report: ok, warn (old or refused), bad (none ever taken)."""
    now = now if now is not None else time.time()
    if not st:
        return "bad", "no agent on this computer"
    answer = st.get("answer", "")
    if answer == "200" and now - st.get("asked", 0) < 300:
        return "ok", "the network has it, %s" % ago(now - st.get("ok", now))
    if st.get("ok"):
        why = answer.split(" ", 1)[1] if " " in answer else answer
        return "warn", "last taken %s; now: %s" % (ago(now - st["ok"]), why or "no answer")
    return "bad", "never taken: %s" % (answer or "no answer yet")


def disk(st):
    """(level, words) of the disk: encrypted, opens by the network's rings or waits to be bound."""
    if not st.get("crypt"):
        return "warn", "not encrypted"
    if st.get("unlock") == "bound":
        return "ok", "encrypted, opens by the network's rings and by its passphrase"
    if st.get("unlock") == "waits":
        return "warn", "encrypted; the network's rings are not bound to it yet"
    return "ok", "encrypted, opens by its passphrase"


def updates(st):
    u = st.get("updates", "")
    if u == "staged":
        return "one waits for a restart"
    if u in ("", "none-staged", "0"):
        return "none waiting" if u else "not known"
    return "%s waiting" % u


def parse_join(command):
    """(the network's CA as base64, the join address) from the command the control page shows; None
    when it is not one."""
    m = JOIN.search(command or "")
    return (m.group(1), m.group(2)) if m else None


def fetch_join(ca_b64, url, opener=None):
    """The agent's installer, checked against the network's CA the command carries. Its key is in it:
    it is kept in memory and goes to root's shell on stdin."""
    ca = base64.b64decode(ca_b64).decode()
    if "BEGIN CERTIFICATE" not in ca:
        raise ValueError("the command carries no certificate")
    folder = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    fd, path = tempfile.mkstemp(prefix="kiwi-ca-", suffix=".pem", dir=folder)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(ca)
        context = ssl.create_default_context(cafile=path)      # this network's CA, and only it
        with (opener or urllib.request.urlopen)(url, timeout=30, context=context) as r:
            return r.read()
    finally:
        os.remove(path)


def as_root(argv, data=None, runner=subprocess.run):
    """A command as root through polkit (pkexec asks for the password in its own window). (exit, words)."""
    r = runner(["pkexec"] + list(argv), input=data, capture_output=True)
    said = (r.stderr or r.stdout or b"").decode(errors="replace").strip().splitlines()
    return r.returncode, (said[-1] if said else "")


def vpn_connections(runner=subprocess.run):
    """{the kiwi connections NetworkManager has: up?}."""
    try:
        r = runner(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show"], capture_output=True, text=True)
    except OSError:
        return {}
    out = {}
    for line in (r.stdout or "").splitlines():
        name, _sep, device = line.partition(":")
        if name.startswith("kiwi-"):
            out[name] = bool(device)
    return out
