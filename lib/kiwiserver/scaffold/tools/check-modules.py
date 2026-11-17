#!/usr/bin/env python3
"""Render every module of this source the way kiwi-server does, on a made-up machine, with
kiwi-modules (KIWI_MODULES: its checkout, default ../kiwi-modules). A module whose template
or settings are wrong fails here, not on someone's machine.

    python3 tools/check-modules.py            # every module
    python3 tools/check-modules.py hello      # one

A module's settings for the check, where it has required ones: modules/<name>/check.yaml,
`settings: {key: value}` (made-up values; never a real password).
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KM = os.path.abspath(os.environ.get("KIWI_MODULES") or os.path.join(HERE, "..", "kiwi-modules"))
sys.path.insert(0, os.path.join(KM, "lib"))
os.environ.setdefault("KIWI_SERVER_HOME", KM)
import yaml  # noqa: E402
from kiwimodules import cli  # noqa: E402

MODULES = os.path.join(HERE, "modules") if os.path.isdir(os.path.join(HERE, "modules")) else HERE
WG = ("[Interface]\nPrivateKey = Y2hlY2stcHJpdmF0ZS1rZXktbm90LXJlYWwtMDAwMDAwMDAwMDA=\nAddress = 10.8.0.25/24\n"
      "DNS = 10.8.0.1\n\n[Peer]\nPublicKey = Y2hlY2stc2VydmVyLXB1YmxpYy1rZXktbm90LXJlYWwtMDAwMDA=\n"
      "AllowedIPs = 0.0.0.0/0\nEndpoint = vpn.example.org:51820\n")


def machine(tmp, name, mod):
    meta = yaml.safe_load(open(os.path.join(MODULES, name, "module.yaml"))) or {}
    types = (meta.get("module") or {}).get("node_types") or ["node"]
    check = os.path.join(MODULES, name, "check.yaml")
    extra = (yaml.safe_load(open(check)) or {}) if os.path.isfile(check) else {}
    tls = {"tls_fullchain": "secrets/fullchain.pem", "tls_privkey": "secrets/privkey.pem"}
    if "node" in types:
        role = "node"
        block = {"modules": ["vpn-client", "reverse-proxy"] + extra.get("with", []) + [name], "reverse-proxy": dict(tls)}
    else:
        role = "master"
        block = {"modules": ["vpn-server", "reverse-proxy"] + extra.get("with", []) + [name], "reverse-proxy": dict(tls),
                 "vpn-server": {"wg_host": "vpn.example.org"}}
    block[name] = extra.get("settings") or {}
    return {"name": "sh3", "domain": "home", "target": "ucore", "timezone": "Europe/Berlin", "role": role, role: block,
            "dns_records": [["10.8.0.1", "gate.home"], ["10.8.0.25", "sh3.home"]], "master_ip": "10.8.0.1",
            "ca_cert": "ca.pem", "seed": "secrets/seed"}


def main(names):
    tmp = tempfile.mkdtemp(prefix="check-modules-")
    try:
        os.makedirs(os.path.join(tmp, "secrets"))
        with open(os.path.join(tmp, "secrets", "seed"), "wb") as f:
            f.write(b"check-seed-0123456789abcdef-0123")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
                        "-subj", "/CN=sh3.home", "-days", "2", "-keyout", os.path.join(tmp, "secrets", "privkey.pem"),
                        "-out", os.path.join(tmp, "secrets", "fullchain.pem")], check=True, capture_output=True)
        shutil.copy(os.path.join(tmp, "secrets", "fullchain.pem"), os.path.join(tmp, "ca.pem"))
        with open(os.path.join(tmp, "secrets", "sh3.home.conf"), "w") as f:
            f.write(WG)
        names = names or sorted(n for n in os.listdir(MODULES) if os.path.isfile(os.path.join(MODULES, n, "module.yaml")))
        bad = 0
        for name in names:
            path = os.path.join(tmp, name + ".yaml")
            with open(path, "w") as f:
                yaml.safe_dump(machine(tmp, name, None), f)
            rc = cli.main(["render", path, "-o", os.path.join(tmp, name + ".bundle"), "--source", "this=" + MODULES])
            print("%s  %s" % ("ok  " if rc == 0 else "FAIL", name), flush=True)
            bad += rc != 0
        print("%d of %d failed" % (bad, len(names)))
        return 1 if bad else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
