#!/usr/bin/env python3
"""Would a machine's reverse proxy start with what the fleet file says now?

    tests/drills/nginx-check.py ~/kiwi/fleet.yaml papaya [medlar ...]

Renders each machine's stack into a temporary folder (nothing is sent anywhere) and has
nginx read its config (`nginx -t`, in a container of the image the machine runs). The
certificates the config names are stood in for by the machine's own. A config nginx refuses
would take every site of the machine down at the next apply: found here first.

Written after a test did that to a control node (15 November 2026).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

if len(sys.argv) < 3:
    sys.exit(__doc__)
fleet, hosts = sys.argv[1], sys.argv[2:]
ks = os.environ.get("KIWI_SERVER", "kiwi-server")
rt = shutil.which("podman") or shutil.which("docker") or sys.exit("drill: podman or docker is needed")
bad = 0
for host in hosts:
    with tempfile.TemporaryDirectory(dir=os.environ.get("XDG_RUNTIME_DIR") or None) as tmp:
        r = subprocess.run([ks, "render", fleet, host, "-o", tmp], capture_output=True, text=True)
        confs = [os.path.join(dp, f) for dp, _dn, fns in os.walk(tmp) for f in fns if f == "nginx.conf"]
        if r.returncode != 0 or not confs:
            print("FAIL  %-8s it does not render: %s" % (host, ((r.stderr or r.stdout).strip().splitlines() or ["no nginx.conf"])[-1][:200]))
            bad += 1
            continue
        conf = confs[0]
        d = os.path.dirname(conf)
        text = open(conf).read()
        image = "docker.io/library/nginx:1"
        m = re.search(r"image:\s*(\S*nginx\S*)", open(next(os.path.join(dp, f) for dp, _dn, fns in os.walk(tmp)
                                                          for f in fns if f == "docker-compose.yml")).read())
        if m:
            image = m.group(1)
        # every certificate the config names, stood in for by the machine's own
        etc = os.path.join(tmp, "etc-nginx")
        for path in set(re.findall(r"ssl_certificate(?:_key)?\s+(/etc/nginx/\S+);", text)):
            dest = os.path.join(etc, path[len("/etc/nginx/"):])
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy(os.path.join(d, "privkey.pem" if "privkey" in path else "fullchain.pem"), dest)
        shutil.copy(conf, os.path.join(etc, "nginx.conf"))
        mime = subprocess.run([rt, "run", "--rm", image, "cat", "/etc/nginx/mime.types"], capture_output=True)
        with open(os.path.join(etc, "mime.types"), "wb") as fh:
            fh.write(mime.stdout)
        r = subprocess.run([rt, "run", "--rm", "--network", "none", "-v", "%s:/etc/nginx:ro,z" % etc, image, "nginx", "-t"],
                           capture_output=True, text=True)
        said = (r.stderr or r.stdout).strip().splitlines()
        if r.returncode == 0:
            print("PASS  %-8s nginx takes its config (%d sites)" % (host, text.count("server_name ") - text.count("server_name _;")))
        else:
            bad += 1
            print("FAIL  %-8s %s" % (host, next((x for x in said if "emerg" in x), said[-1] if said else "nginx -t failed")[:220]))
print("%d of %d would not start" % (bad, len(hosts)))
sys.exit(1 if bad else 0)
