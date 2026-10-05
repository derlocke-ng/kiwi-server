"""An internal certificate authority and per-host wildcard certificates.

ksslgen.sh, built in: one CA for the fleet (secrets/ca/kiwiCA.key + .pem next
to the fleet file) and, for every host that runs the reverse proxy and gives
no certificate of its own, a certificate for <hostname> and *.<hostname>
signed by it. The CA certificate is installed on every machine the fleet
builds, so the nodes trust each other's services; import kiwiCA.pem on your
own devices once and the browser warnings are gone.

Everything goes through the openssl binary — no Python crypto dependency.
"""
import os
import subprocess

from . import util
from .util import KiwiError

CA_SUBJECT = "/C=NZ/ST=kiwi/L=kiwi/O=kiwi/OU=kiwi/CN=kiwiCA"
DAYS = 3650        # the CA
CERT_DAYS = 825    # a host certificate: the longest lifetime browsers still accept


def _openssl(args, **kw):
    try:
        return subprocess.run(["openssl"] + args, check=True, capture_output=True, text=True, **kw)
    except FileNotFoundError:
        raise KiwiError("openssl is required to create certificates")
    except subprocess.CalledProcessError as e:
        raise KiwiError("openssl %s failed: %s" % (args[0], (e.stderr or "").strip().splitlines()[-1:]))


def ensure_ca(ca_dir, days=DAYS, name_constraints=()):
    """kiwiCA.key / kiwiCA.pem in ca_dir, created on first use. With name
    constraints the CA can only sign names under the given domains — a
    stolen kiwiCA.key is then worthless for anything else, which matters
    because every machine built trusts it."""
    key = os.path.join(ca_dir, "kiwiCA.key")
    pem = os.path.join(ca_dir, "kiwiCA.pem")
    if os.path.isfile(key) and os.path.isfile(pem):
        return key, pem
    if os.path.isfile(key) != os.path.isfile(pem):
        raise KiwiError("%s: only one of kiwiCA.key / kiwiCA.pem exists — restore the other or remove both" % ca_dir)
    os.makedirs(ca_dir, mode=0o700, exist_ok=True)
    util.say("creating the fleet CA in %s (keep kiwiCA.key private, import kiwiCA.pem on your devices)" % ca_dir)
    _openssl(["genrsa", "-out", key + ".tmp", "4096"])
    os.chmod(key + ".tmp", 0o600)
    os.replace(key + ".tmp", key)
    argv = ["req", "-x509", "-new", "-nodes", "-key", key, "-sha256", "-days", str(days),
            "-subj", CA_SUBJECT, "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign"]
    domains = [str(d).strip().lstrip(".") for d in (name_constraints or []) if str(d).strip()]
    if domains:
        argv += ["-addext", "nameConstraints=critical," + ",".join("permitted;DNS:." + d for d in domains)]
    _openssl(argv + ["-out", pem])
    return key, pem


def cert_names(pem_path):
    out = _openssl(["x509", "-in", pem_path, "-noout", "-ext", "subjectAltName"]).stdout
    return [p.strip()[4:] for p in out.replace("\n", ",").split(",") if p.strip().startswith("DNS:")]


def cert_valid(pem_path, min_days=30):
    r = subprocess.run(["openssl", "x509", "-in", pem_path, "-noout", "-checkend", str(min_days * 86400)],
                       capture_output=True)
    return r.returncode == 0


def ensure_host_cert(ca_dir, hostname, days=CERT_DAYS, ca_days=DAYS, name_constraints=()):
    """(privkey.pem, fullchain.pem) for hostname and *.hostname, reused while
    they exist, cover the names and have more than 30 days left."""
    ca_key, ca_pem = ensure_ca(ca_dir, ca_days, name_constraints)
    d = os.path.join(ca_dir, "hosts", hostname)
    key = os.path.join(d, "privkey.pem")
    full = os.path.join(d, "fullchain.pem")
    crt = os.path.join(d, "cert.pem")
    want = {hostname, "*." + hostname}
    if os.path.isfile(key) and os.path.isfile(full) and os.path.isfile(crt) \
            and cert_valid(crt) and set(cert_names(crt)) >= want:
        return key, full
    os.makedirs(d, mode=0o700, exist_ok=True)
    util.say("issuing a certificate for %s and *.%s" % (hostname, hostname))
    csr = os.path.join(d, "request.csr")
    ext = os.path.join(d, "ext.cnf")
    util.write_text(ext, "authorityKeyIdentifier=keyid,issuer\nbasicConstraints=CA:FALSE\n"
                         "keyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
                         "subjectAltName=DNS:%s,DNS:*.%s\n" % (hostname, hostname))
    _openssl(["genrsa", "-out", key + ".tmp", "4096"])
    os.chmod(key + ".tmp", 0o600)
    os.replace(key + ".tmp", key)
    _openssl(["req", "-new", "-key", key, "-subj", "/C=NZ/ST=kiwi/L=kiwi/O=kiwi/OU=kiwi/CN=%s" % hostname,
              "-out", csr])
    _openssl(["x509", "-req", "-in", csr, "-CA", ca_pem, "-CAkey", ca_key, "-CAcreateserial",
              "-days", str(days), "-sha256", "-extfile", ext, "-out", crt])
    util.write_text(full, util.read_text(crt) + util.read_text(ca_pem), 0o644)
    for f in (csr, ext):
        if os.path.exists(f):
            os.unlink(f)
    return key, full
