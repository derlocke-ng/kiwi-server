"""Run the build tools natively when installed, else inside a container.

butane, coreos-installer and xorriso are not in the Silverblue/Bluefin base
image, and layering them is exactly what an ostree desktop tries to avoid. So
every tool call goes through here: a tool found on $PATH runs directly; one
that is missing runs in the kiwi-server toolchain image (container/Dockerfile)
with the directories it touches bind-mounted at the SAME paths, so command
lines are identical either way.
"""
import os
import subprocess

from . import util
from .util import KiwiError

TOOLS = ("butane", "coreos-installer", "xorriso", "cpio", "gzip", "curl")
IMAGE = "localhost/kiwi-server-toolchain"


class Toolchain:
    def __init__(self, mode="auto", image=None, runtime=None, verbose=False):
        if mode not in ("auto", "native", "container"):
            raise KiwiError("toolchain mode must be auto, native or container")
        self.mode = mode
        self.image = image or os.environ.get("KIWI_SERVER_IMAGE") or IMAGE
        self._runtime = runtime or os.environ.get("KIWI_SERVER_RUNTIME")
        self.verbose = verbose
        self._image_ok = None

    # ---- discovery ---------------------------------------------------------------
    def runtime(self):
        if self._runtime:
            return self._runtime if util.which(self._runtime) else None
        for rt in ("podman", "docker"):
            if util.which(rt):
                self._runtime = rt
                return rt
        return None

    def image_present(self):
        if self._image_ok is None:
            rt = self.runtime()
            if not rt:
                self._image_ok = False
            else:
                r = subprocess.run([rt, "image", "exists", self.image] if rt == "podman"
                                   else [rt, "image", "inspect", self.image],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._image_ok = r.returncode == 0
        return self._image_ok

    def how(self, tool):
        """'native', 'container' or None (and why not, as the second item)."""
        if self.mode != "container" and util.which(tool):
            return "native", ""
        if self.mode == "native":
            return None, "%s is not installed" % tool
        rt = self.runtime()
        if not rt:
            return None, "%s is not installed and neither podman nor docker is available" % tool
        if not self.image_present():
            return None, ("%s is not installed and the toolchain image %s is missing — "
                          "run: kiwi-server toolchain build" % (tool, self.image))
        return "container", ""

    def status(self):
        return {t: self.how(t) for t in TOOLS}

    # ---- execution -----------------------------------------------------------------
    def argv_for(self, argv, mounts=(), cwd=None):
        how, why = self.how(argv[0])
        if how is None:
            raise KiwiError(why)
        if how == "native":
            return list(argv)
        rt = self.runtime()
        cmd = [rt, "run", "--rm", "--pull=never"]
        if rt == "docker":
            cmd += ["--user", "%d:%d" % (os.getuid(), os.getgid())]
        seen = set()
        for m in list(mounts) + ([cwd] if cwd else []):
            m = os.path.abspath(m)
            if m in seen:
                continue
            seen.add(m)
            os.makedirs(m, exist_ok=True)
            cmd += ["-v", "%s:%s:z" % (m, m)]
        if cwd:
            cmd += ["-w", os.path.abspath(cwd)]
        cmd.append(self.image)
        return cmd + list(argv)

    def run(self, argv, mounts=(), cwd=None, capture=False, check=True, stdin=None):
        cmd = self.argv_for(argv, mounts, cwd)
        if self.verbose:
            util.log("$ " + " ".join(util.quote(c) for c in cmd))
        r = subprocess.run(cmd, cwd=cwd, input=stdin,
                           stdout=subprocess.PIPE if capture else None,
                           stderr=subprocess.PIPE if capture else None, text=True)
        if check and r.returncode != 0:
            tail = (r.stderr or "").strip().splitlines()[-5:] if capture else []
            raise KiwiError("%s failed (exit %d)%s" % (argv[0], r.returncode,
                                                       ("\n  " + "\n  ".join(tail)) if tail else ""))
        return r

    def build_image(self, container_dir):
        rt = self.runtime()
        if not rt:
            raise KiwiError("building the toolchain image needs podman or docker")
        df = os.path.join(container_dir, "Dockerfile")
        if not os.path.isfile(df):
            raise KiwiError("Dockerfile not found: %s" % df)
        util.say("building %s with %s (butane, coreos-installer, xorriso)" % (self.image, rt))
        r = subprocess.run([rt, "build", "-t", self.image, "-f", df, container_dir])
        if r.returncode != 0:
            raise KiwiError("container build failed")
        self._image_ok = True
