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


def _ensure_ca(ca_dir, days=DAYS, name_constraints=()):
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


def ca_permitted(pem):
    """The domains a CA certificate (PEM text) may sign names under: its name constraint's
    permitted DNS entries, without the leading dot. None: no constraint, it may sign any name."""
    if "-----BEGIN CERTIFICATE-----" not in str(pem or ""):
        raise KiwiError("not a certificate")
    out = _openssl(["x509", "-noout", "-ext", "nameConstraints"], input=pem).stdout
    if "Name Constraints" not in out:
        return None
    permitted, inside = [], False
    for line in out.splitlines():
        line = line.strip()
        if line.endswith(":") and not line.startswith("DNS:"):
            inside = line == "Permitted:"
        elif inside and line.startswith("DNS:"):
            permitted.append(line[4:].strip().lstrip(".").lower())
    return permitted


def public_key(pem, private=False):
    """The public key (PEM text) of a certificate, or of a private key file (private=True)."""
    if private:
        return _openssl(["pkey", "-in", pem, "-pubout"]).stdout
    return _openssl(["x509", "-noout", "-pubkey"], input=pem).stdout


def sign_for(ca_dir, key_path, names, days=CERT_DAYS):
    """A certificate (leaf, then the CA: a fullchain) of the CA in ca_dir for exactly these names,
    for the key at key_path, which stays where it is: another network's CA signing a machine's
    names as that network calls it (federation, translated at the link). The CA must exist."""
    ca_key, ca_pem = os.path.join(ca_dir, "kiwiCA.key"), os.path.join(ca_dir, "kiwiCA.pem")
    if not (os.path.isfile(ca_key) and os.path.isfile(ca_pem)):
        raise KiwiError("%s: no CA there to sign with" % ca_dir)
    import tempfile
    with tempfile.TemporaryDirectory(prefix="kiwi-sign-") as tmp:
        csr, ext, crt = (os.path.join(tmp, n) for n in ("request.csr", "ext.cnf", "cert.pem"))
        util.write_text(ext, "authorityKeyIdentifier=keyid,issuer\nbasicConstraints=CA:FALSE\n"
                             "keyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
                             "subjectAltName=%s\n" % ",".join("DNS:%s" % n for n in names))
        _openssl(["req", "-new", "-key", key_path, "-subj", "/O=kiwi/CN=%s" % names[0], "-out", csr])
        _openssl(["x509", "-req", "-in", csr, "-CA", ca_pem, "-CAkey", ca_key, "-CAcreateserial",
                  "-days", str(days), "-sha256", "-extfile", ext, "-out", crt])
        return util.read_text(crt) + util.read_text(ca_pem)


def cert_valid(pem_path, min_days=30):
    r = subprocess.run(["openssl", "x509", "-in", pem_path, "-noout", "-checkend", str(min_days * 86400)],
                       capture_output=True)
    return r.returncode == 0


def _ensure_host_cert(ca_dir, hostname, days=CERT_DAYS, ca_days=DAYS, name_constraints=(), aliases=()):
    """(privkey.pem, fullchain.pem) for hostname and *.hostname, reused while
    they exist, cover the names and have more than 30 days left. `aliases`: other names of the
    machine (and *. of each), the name a linked network with the same domain gives it."""
    ca_key, ca_pem = _ensure_ca(ca_dir, ca_days, name_constraints)
    d = os.path.join(ca_dir, "hosts", hostname)
    key = os.path.join(d, "privkey.pem")
    full = os.path.join(d, "fullchain.pem")
    crt = os.path.join(d, "cert.pem")
    want = {hostname, "*." + hostname} | {n for a in aliases for n in (a, "*." + a)}
    if os.path.isfile(key) and os.path.isfile(full) and os.path.isfile(crt) \
            and cert_valid(crt) and set(cert_names(crt)) >= want:
        return key, full
    os.makedirs(d, mode=0o700, exist_ok=True)
    util.say("issuing a certificate for %s and *.%s%s" % (hostname, hostname, "".join(" and %s" % a for a in aliases)))
    csr = os.path.join(d, "request.csr")
    ext = os.path.join(d, "ext.cnf")
    util.write_text(ext, "authorityKeyIdentifier=keyid,issuer\nbasicConstraints=CA:FALSE\n"
                         "keyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
                         "subjectAltName=%s\n" % ",".join("DNS:%s" % n for n in [hostname, "*." + hostname] +
                                                            [n for a in aliases for n in (a, "*." + a)]))
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


def _ensure_names_cert(ca_dir, label, names, days=CERT_DAYS, ca_days=DAYS, name_constraints=(), own=False):
    """(privkey.pem, fullchain.pem) for exactly these names, kept under hosts/<label>: reused
    while they exist, cover the names and have more than 30 days left. For names that are
    not a machine's own, like the addresses of its hidden services. `own`: signed by itself,
    not by the fleet's CA (a CA limited to the fleet's names cannot vouch for others)."""
    names = sorted(set(names))
    if not names:
        raise KiwiError("a certificate needs a name")
    d = os.path.join(ca_dir, "hosts", label)
    if own:
        key, full = os.path.join(d, "privkey.pem"), os.path.join(d, "fullchain.pem")
        if os.path.isfile(key) and os.path.isfile(full) and cert_valid(full) and set(cert_names(full)) == set(names):
            return key, full
        os.makedirs(d, mode=0o700, exist_ok=True)
        util.say("making a certificate of its own for %s" % ", ".join(names))
        _openssl(["req", "-x509", "-newkey", "rsa:4096", "-nodes", "-days", str(max(days, 3650)), "-sha256",
                  "-subj", "/C=NZ/ST=kiwi/L=kiwi/O=kiwi/OU=kiwi/CN=%s" % label,
                  "-addext", "subjectAltName=%s" % ",".join("DNS:" + n for n in names),
                  "-keyout", key + ".tmp", "-out", full])
        os.chmod(key + ".tmp", 0o600)
        os.replace(key + ".tmp", key)
        return key, full
    ca_key, ca_pem = _ensure_ca(ca_dir, ca_days, name_constraints)
    key, full, crt = os.path.join(d, "privkey.pem"), os.path.join(d, "fullchain.pem"), os.path.join(d, "cert.pem")
    if os.path.isfile(key) and os.path.isfile(full) and os.path.isfile(crt) \
            and cert_valid(crt) and set(cert_names(crt)) == set(names):
        return key, full
    os.makedirs(d, mode=0o700, exist_ok=True)
    util.say("issuing a certificate for %s" % ", ".join(names))
    csr, ext = os.path.join(d, "request.csr"), os.path.join(d, "ext.cnf")
    util.write_text(ext, "authorityKeyIdentifier=keyid,issuer\nbasicConstraints=CA:FALSE\n"
                         "keyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
                         "subjectAltName=%s\n" % ",".join("DNS:" + n for n in names))
    _openssl(["genrsa", "-out", key + ".tmp", "4096"])
    os.chmod(key + ".tmp", 0o600)
    os.replace(key + ".tmp", key)
    _openssl(["req", "-new", "-key", key, "-subj", "/C=NZ/ST=kiwi/L=kiwi/O=kiwi/OU=kiwi/CN=%s" % label, "-out", csr])
    _openssl(["x509", "-req", "-in", csr, "-CA", ca_pem, "-CAkey", ca_key, "-CAcreateserial",
              "-days", str(days), "-sha256", "-extfile", ext, "-out", crt])
    util.write_text(full, util.read_text(crt) + util.read_text(ca_pem), 0o644)
    for f in (csr, ext):
        if os.path.exists(f):
            os.unlink(f)
    return key, full


def ensure_ca(ca_dir, days=DAYS, name_constraints=()):
    """The fleet's CA (_ensure_ca), made by one command at a time: two at once made two CAs."""
    with util.dir_lock(ca_dir):
        return _ensure_ca(ca_dir, days, name_constraints)


def ensure_host_cert(ca_dir, hostname, days=CERT_DAYS, ca_days=DAYS, name_constraints=(), aliases=()):
    """A machine's certificate (_ensure_host_cert), one command at a time per CA folder: two at once
    could keep the key of one with the certificate of the other."""
    with util.dir_lock(ca_dir):
        return _ensure_host_cert(ca_dir, hostname, days, ca_days, name_constraints, tuple(aliases))


def ensure_names_cert(ca_dir, label, names, days=CERT_DAYS, ca_days=DAYS, name_constraints=(), own=False):
    """A certificate for these names (_ensure_names_cert), one command at a time per CA folder."""
    with util.dir_lock(ca_dir):
        return _ensure_names_cert(ca_dir, label, names, days, ca_days, name_constraints, own)
