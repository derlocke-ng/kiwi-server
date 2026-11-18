"""A kiwi-server that stays running beside the app, so that a click does not start a new one.

Each command of the app was a new kiwi-server: Python started, the fleet and the modules were
read again, every machine rendered again for `list` (four seconds for six machines). The agent
answers the commands that only read, one after the other, in the same process, and keeps the
answer of `list` until something it reads changes: the fleet file, its folder (the state of
each machine, the secrets), or the installed kiwi-server. Everything else (apply, build, a
change) still runs as a command of its own, as before.

    kiwi-server agent [--parent PID]

It listens on a socket only its user may open ($XDG_RUNTIME_DIR/kiwi-server-agent.sock); one
JSON line in, {"argv": [...]} or {"forget": true}, one JSON line out, {"rc", "out", "err"}. It ends
when the app it came with ends (--parent), or after half an hour without a question.
"""
import contextlib
import io
import json
import os
import socket
import sys
import threading
import time

from . import VERSION

READS = {("list",), ("logins",), ("version",), ("names",), ("validate",), ("secrets", "status"), ("sources", "list")}
IDLE = 1800


def socket_path():
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(base, "kiwi-server-agent.sock")


def reads(argv):
    """Is this a command the agent answers? Only the ones that change nothing."""
    if not argv:
        return False
    first = (argv[0],)
    two = tuple(argv[:1]) + tuple(a for a in argv[2:3] if not a.startswith("-"))
    return first in READS or (len(argv) >= 3 and two in READS)


def _stamp(argv):
    """What the answer depends on: the fleet file, everything directly in its folder and in
    secrets/, and this kiwi-server's version."""
    fleet = next((a for a in argv[1:] if a.endswith((".yaml", ".yml"))), "")
    if not fleet or not os.path.isfile(fleet):
        return None
    folder = os.path.dirname(os.path.abspath(fleet))
    marks = [VERSION]
    for d in (folder, os.path.join(folder, "secrets"), os.path.join(folder, "control")):
        try:
            with os.scandir(d) as it:
                marks += sorted("%s:%d" % (e.name, e.stat(follow_symlinks=False).st_mtime_ns) for e in it)
        except OSError:
            pass
    return "|".join(marks)


class Agent:
    def __init__(self):
        from . import cli
        self.cli = cli
        self.lock = threading.Lock()
        self.cache = {}
        self.last = time.time()

    def answer(self, request):
        self.last = time.time()
        if request.get("forget"):
            self.cache.clear()
            return {"rc": 0, "out": "", "err": ""}
        argv = [str(a) for a in request.get("argv") or []]
        if not reads(argv):
            return {"rc": 2, "out": "", "err": "the agent answers only commands that read"}
        key = tuple(argv)
        stamp = _stamp(argv) if argv[0] == "list" else None
        if stamp and key in self.cache and self.cache[key][0] == stamp:
            return self.cache[key][1]
        with self.lock:                 # the CLI keeps a little state in its modules: one at a time
            out, err = io.StringIO(), io.StringIO()
            cwd = os.getcwd()
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        rc = self.cli.main(argv) or 0
                    except SystemExit as e:
                        rc = e.code if isinstance(e.code, int) else 1
            finally:
                os.chdir(cwd)
        result = {"rc": rc, "out": out.getvalue(), "err": err.getvalue()}
        if stamp and rc == 0:
            self.cache[key] = (_stamp(argv), result)
        return result


def serve(parent=None):
    path = socket_path()
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    old = os.umask(0o177)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(path)
    os.umask(old)
    os.chmod(path, 0o600)
    s.listen(8)
    s.settimeout(5)
    agent = Agent()

    def handle(conn):
        with conn:
            try:
                line = conn.makefile("r").readline()
                reply = agent.answer(json.loads(line or "{}"))
            except (ValueError, OSError) as e:
                reply = {"rc": 2, "out": "", "err": str(e)}
            try:
                conn.sendall((json.dumps(reply) + "\n").encode())
            except OSError:
                pass
    try:
        while True:
            if parent and not os.path.exists("/proc/%d" % parent):
                return
            if time.time() - agent.last > IDLE:
                return
            try:
                conn, _a = s.accept()
            except socket.timeout:
                continue
            threading.Thread(target=handle, args=(conn,), daemon=True).start()
    finally:
        s.close()
        try:
            os.unlink(path)
        except OSError:
            pass


def ask(argv, timeout=300):
    """The agent's answer (rc, out, err), or None when no agent runs."""
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(timeout)
        c.connect(socket_path())
    except OSError:
        return None
    with c:
        c.sendall((json.dumps({"argv": list(argv)}) + "\n").encode())
        got = c.makefile("r").readline()
    try:
        r = json.loads(got)
        return r["rc"], r["out"], r["err"]
    except (ValueError, KeyError):
        return None


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else None)
