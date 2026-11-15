#!/usr/bin/env python3
"""Do game servers answer at an address? Asked the way a game's own client asks.

    tests/drills/game-ports.py 5.45.103.177 minecraft:25565 zandronum:10666

For a server that is open to the Internet (`public: true`), the address is the master's: the
question then goes through the master to the machine and back. Minecraft (Java) is asked the
server list's question over TCP and says its version, its players and its words; Zandronum is
asked the launcher's question over UDP. Any other `name:port` is tried as a TCP port that
opens. Nothing is changed and nobody joins.
"""
import json
import socket
import struct
import sys
import time

if len(sys.argv) < 3:
    sys.exit(__doc__)
addr, bad = sys.argv[1], 0


def varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def read_varint(s):
    n = shift = 0
    while True:
        b = s.recv(1)
        if not b:
            raise OSError("the server closed the connection")
        n |= (b[0] & 0x7F) << shift
        shift += 7
        if not b[0] & 0x80:
            return n


for what in sys.argv[2:]:
    game, _, port = what.partition(":")
    port = int(port)
    try:
        if game == "minecraft":
            with socket.create_connection((addr, port), 15) as s:
                s.settimeout(15)
                host = addr.encode()
                hello = varint(0) + varint(767) + varint(len(host)) + host + struct.pack(">H", port) + varint(1)
                s.sendall(varint(len(hello)) + hello + varint(1) + varint(0))
                read_varint(s); read_varint(s)
                n, data = read_varint(s), b""
                while len(data) < n:
                    data += s.recv(n - len(data))
            st = json.loads(data.decode())
            words = st.get("description")
            words = words.get("text") if isinstance(words, dict) else words
            said = "version %s, %s of %s players, says %r" % (st["version"]["name"], st["players"]["online"], st["players"]["max"], words)
        elif game == "zandronum":
            u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            u.settimeout(8)
            # the launcher's challenge; a first byte of 255 says: not packed
            u.sendto(b"\xff" + struct.pack("<iII", 199, 0x1 | 0x8, int(time.time()) & 0xFFFFFFFF), (addr, port))
            data, _who = u.recvfrom(4096)
            u.close()
            said = "answers the launcher's question (%d bytes)" % len(data)
        else:
            socket.create_connection((addr, port), 10).close()
            said = "the port opens"
        print("PASS  %-22s %s" % (what, said))
    except (OSError, ValueError, KeyError) as e:
        bad += 1
        print("FAIL  %-22s %s: %s" % (what, type(e).__name__, e))
print("%d of %d did not answer at %s" % (bad, len(sys.argv) - 2, addr))
sys.exit(1 if bad else 0)
