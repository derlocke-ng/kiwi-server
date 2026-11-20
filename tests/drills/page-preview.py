#!/usr/bin/env python3
"""The control page with a made-up network, to look at and click on this computer.

A throwaway fleet in a temporary folder, the page on 127.0.0.1 and machines that exist only
as the answers of a stand-in for `kiwi-server list` and `status` (madeup.py): five that are
fine, one that wants a look, one that does not answer. Nothing real is asked or changed.

    podman run --rm --network host -v "$PWD":/src:ro,z -w /src --entrypoint python3 -e PYTHONPATH=/src/lib \
        ghcr.io/derlocke-ng/kiwi-server-control:2 tests/drills/page-preview.py [port]

The control image has what the page needs (FastAPI, uvicorn, age). Then open
http://127.0.0.1:8741: the password is printed.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "..", "lib"), os.path.join(HERE, ".."), HERE]
import test_kiwiserver as t                                  # noqa: E402
import madeup                                                # noqa: E402


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8741
    t.remote.free_port = lambda: port
    case = t.TestWeb("test_the_page_changes_its_password_and_replaces_the_keys")
    case.setUp()
    listed, status = madeup.answers()
    stand_in = os.path.join(case.tmp, "stand-in.py")
    with open(stand_in, "w") as fh:
        fh.write("import json, sys\nsys.path.insert(0, %r)\nimport madeup\nwhat = sys.argv[1] if len(sys.argv) > 1 else ''\n"
                 "if what == 'move' and '--porcelain' in sys.argv:      # the plan the Move page shows\n"
                 "    print(json.dumps(madeup.move_plan(*sys.argv[3:6])))\n"
                 "else:\n"
                 "    print(%r if what == 'list' else %r if what == 'status' else 'ran ' + ' '.join(sys.argv[1:]))\n"
                 % (HERE, json.dumps(listed), json.dumps(status)))
    case.c.kiwi = [sys.executable, stand_in]
    # two hidden services, made up: the Services page shows their addresses
    case.c.onion = lambda: {"%s.%s" % (x["service"], x["host"]): x for x in madeup.hidden()["services"]}
    case.c.accounts = madeup.accounts                         # a made-up sign-up: people, requests, codes
    case.c.has_jabber_join = lambda: True
    case.ask("/login", {"password": case.PW})               # the store opens: the devices go into it, not beside it
    case.c.refresh_status()
    with open(case.c.health_file, "w") as fh:                # for /status, and for Home's services that do not answer
        json.dump(madeup.health(time.time()), fh)
    os.makedirs(os.path.join(case.f.dir, "secrets", "devices"), exist_ok=True)
    for d, text in madeup.devices():
        with open(os.path.join(case.f.dir, "secrets", "devices", d + ".conf"), "w") as fh:
            fh.write(text)
    print("http://127.0.0.1:%d   password: %s   (Ctrl+C ends it)" % (port, case.PW), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        case.doCleanups()
        case.tearDown()


if __name__ == "__main__":
    main()
