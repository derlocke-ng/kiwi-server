#!/usr/bin/env python3
"""The unit tests, several at the same time.

One after the other they take nine minutes in CI and six and a half here; most of that is
waiting (a page started per test, ssh-keygen, age). This runs them in N processes, each with its
share of the tests, and says at the end what failed, with that shard's output.

    python3 tests/run-parallel.py            as many processes as this computer has cores
    KIWI_TEST_JOBS=4 python3 tests/run-parallel.py
    python3 tests/run-parallel.py -k look    only tests whose name contains "look" (as unittest -k)

Exit status 0 when every test passed.
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def tests(suite, out):
    """The id of every test discovery finds (module.Class.method); one that did not import as "!id"."""
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            tests(t, out)
        elif t.__class__.__module__ == "unittest.loader":       # a module that did not import: unittest names it
            out.append("!" + t.id())
        else:
            out.append(t.id())
    return out


def main():
    args = sys.argv[1:]
    jobs = max(1, int(os.environ.get("KIWI_TEST_JOBS") or os.cpu_count() or 2))
    found = sorted(tests(unittest.defaultTestLoader.discover(HERE), []))
    broken = [n[1:] for n in found if n.startswith("!")]
    if broken:
        print("could not be loaded: %s" % ", ".join(broken))
        return 1
    # dealt out like cards, in the order of their names: every class's tests are spread over all the
    # shards, so a slow class slows all of them a little and none of them a lot
    n = min(jobs, len(found)) or 1
    shards = [(len(found[i::n]), found[i::n]) for i in range(n)]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [HERE, os.environ.get("PYTHONPATH")])))
    began, running = time.time(), []
    for i, (_n, names) in enumerate(shards):
        log = tempfile.NamedTemporaryFile("w+", prefix="kiwi-tests-%d-" % i, suffix=".log", delete=False)
        p = subprocess.Popen([sys.executable, "-m", "unittest"] + args + names, cwd=HERE, env=env,
                             stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        running.append((i, p, log, names))
    failed = 0
    for i, p, log, names in running:
        rc = p.wait()
        log.seek(0)
        text = log.read()
        log.close()
        os.unlink(log.name)
        tail = [line for line in text.splitlines() if line.startswith(("Ran ", "OK", "FAILED", "NO TESTS RAN"))]
        nothing = "NO TESTS RAN" in text or "Ran 0 tests" in text
        print("shard %d (%d tests): %s" % (i + 1, len(names), " ".join(tail) or "no result"))
        if rc != 0 and not (nothing and args):          # with -k a shard may have nothing to run
            failed += 1
            print("---- shard %d ----\n%s\n---- end of shard %d ----" % (i + 1, text, i + 1))
    print("%s in %d s, %d at once" % ("FAILED" if failed else "OK", time.time() - began, len(shards)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
