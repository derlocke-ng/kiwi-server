"""Where everything stands in the picture of the network: the master out on the Internet with its
standby beside it and the networks linked to it, the machines in a zone per site (at home, and
every other place), and the tunnel each machine holds to the master. Pure geometry, no drawing:
the control page draws it as SVG (web.picture), the app with Cairo (NetworkPicture); both from
this, so that they show the same. The app loads this file by its path: it imports nothing.

    layout(master, machines, standbys=(), linked=(), width=860, narrow=False)

master, each standby: {name, state, cap}; each machine: {name, state, cap, up, site}; each linked
network: {name, cap}. Returns {w, h, boxes, tunnels, sides, zones, trunk}:
  boxes    {name, state, cap, kind (master, standby, linked, machine), up, x, y, w, h}
  tunnels  (sx, sy, ex, ez, ey, up): from the master's lower edge down to a machine
  branches (tx, y, ex, up): off the trunk, in the narrow picture
  sides    (x1, y1, x2, y2, kind): the master to its standby, to a linked network
  zones    {label, x, y, w, h}
  trunk    (x, y1, y2) or None: the line down the narrow picture
"""

BW, BH, GAP, MW, MH, SW, ZP, ZGAP, PAD = 172, 54, 18, 260, 58, 190, 34, 16, 10


def zone_label(site):
    return "At home" if site in ("", "home") else str(site)[:1].upper() + str(site)[1:]


def _zones(machines):
    """[(site, [machines])], home first, then the others as they first come."""
    order, out = [], {}
    for m in machines:
        s = m.get("site") or "home"
        if s not in out:
            out[s] = []
            order.append(s)
        out[s].append(m)
    order.sort(key=lambda s: s != "home")
    return [(s, out[s]) for s in order]


def _box(boxes, item, kind, x, y, w, h):
    boxes.append(dict(item, kind=kind, x=int(x), y=int(y), w=int(w), h=int(h), up=item.get("up")))
    return boxes[-1]


def layout(master, machines, standbys=(), linked=(), width=860, narrow=False):
    zones = _zones(list(machines))
    if not narrow:
        wide = _wide(master, zones, list(standbys), list(linked), width)
        if wide:
            return wide
    return _narrow(master, zones, list(standbys), list(linked), min(width, 560) if not narrow else width)


def _top(master, standbys, linked, width, boxes, sides):
    """The master's row: its standbys to the left, the linked networks to the right; on a row
    of their own under it when one row is too narrow. Returns the master's box and the row's foot."""
    side = len(standbys) + len(linked)
    one_row = MW + side * (SW + 24) <= width - 2 * PAD
    if one_row:
        x = (width - (MW + side * (SW + 24))) / 2
        for s in standbys:
            b = _box(boxes, s, "standby", x, 10, SW, MH - 6)
            x += SW + 24
        m = _box(boxes, master, "master", x, 8, MW, MH)
        x += MW + 24
        for n in linked:
            _box(boxes, n, "linked", x, 10, SW, MH - 6)
            x += SW + 24
        for b in boxes:
            if b["kind"] == "standby":
                sides.append((b["x"] + b["w"], b["y"] + b["h"] / 2, m["x"], m["y"] + m["h"] / 2, "standby"))
            elif b["kind"] == "linked":
                sides.append((m["x"] + m["w"], m["y"] + m["h"] / 2, b["x"], b["y"] + b["h"] / 2, "linked"))
        return m, 8 + MH
    m = _box(boxes, master, "master", (width - MW) / 2, 8, MW, MH)
    if not side:
        return m, 8 + MH
    sw = min(SW, (width - 2 * PAD - (side - 1) * 16) / side)
    x, y = (width - (side * sw + (side - 1) * 16)) / 2, 8 + MH + 22
    for kind, items in (("standby", standbys), ("linked", linked)):
        for s in items:
            b = _box(boxes, s, kind, x, y, sw, MH - 6)
            sides.append((b["x"] + b["w"] / 2, b["y"], m["x"] + m["w"] / 2, m["y"] + m["h"], kind))
            x += sw + 16
    return m, y + MH - 6


def _wide(master, zones, standbys, linked, width):
    """The zones side by side, each machine in rows; None when they do not fit at this width."""
    nz = len(zones)
    avail = width - 2 * PAD - nz * 2 * ZP - (nz - 1) * ZGAP
    columns = int((avail + GAP * nz) // (BW + GAP))
    if columns < nz:
        return None
    cols = [1] * nz
    extra = min(columns, 6 * nz) - nz
    while extra > 0:                    # one more column where the most machines wait for one
        need = [len(ms) - c for (_s, ms), c in zip(zones, cols)]
        i = max(range(nz), key=lambda k: need[k])
        if need[i] <= 0:
            break
        cols[i] += 1
        extra -= 1
    # a zone whose machines take more than two rows makes the picture a list instead
    if any(len(ms) > 2 * c - (1 if c > 1 else 0) for (_s, ms), c in zip(zones, cols)):
        return None
    boxes, sides, tunnels, zlist = [], [], [], []
    m, foot = _top(master, standbys, linked, width, boxes, sides)
    zy = foot + 58
    widths = [c * BW + (c - 1) * GAP + 2 * ZP for c in cols]
    x = (width - (sum(widths) + (nz - 1) * ZGAP)) / 2
    bottom = zy
    placed = []
    for (site, ms), c, zw in zip(zones, cols, widths):
        rows, left = [], list(ms)
        while left:                     # later rows: one less, between the ones above
            take = c if not rows or c == 1 else c - 1
            rows.append(left[:take])
            left = left[take:]
        y = zy + 30
        for row in rows:
            rx = x + (zw - (len(row) * BW + (len(row) - 1) * GAP)) / 2
            for mm in row:
                placed.append(_box(boxes, mm, "machine", rx, y, BW, BH))
                rx += BW + GAP
            y += BH + GAP
        zlist.append({"label": zone_label(site), "x": int(x), "y": int(zy), "w": int(zw), "h": int(y - GAP + 22 - zy)})
        bottom = max(bottom, y - GAP + 22)
        x += zw + ZGAP
    for z in zlist:                     # side by side: one height
        z["h"] = int(bottom - zy)
    placed.sort(key=lambda b: (b["x"], b["y"]))
    for i, b in enumerate(placed):
        sx = m["x"] + 24 + (m["w"] - 48) * (i + 0.5) / len(placed)
        tunnels.append((sx, m["y"] + m["h"], b["x"] + b["w"] / 2, zy + 12, b["y"], bool(b.get("up"))))
    return {"w": int(width), "h": int(bottom + 8), "boxes": boxes, "tunnels": tunnels, "branches": [], "sides": sides,
            "zones": zlist, "trunk": None}


def _narrow(master, zones, standbys, linked, width):
    """One under the other: the master, its standbys and linked networks, then each site with its
    machines; a line down from the master and a branch to each machine."""
    boxes, sides, branches, zlist = [], [], [], []
    w = width - 2 * PAD
    x0 = PAD
    m = _box(boxes, master, "master", x0, 8, min(MW, w), MH)
    y = 8 + MH + 16
    for kind, items in (("standby", standbys), ("linked", linked)):
        for s in items:
            b = _box(boxes, s, kind, x0 + 48, y, w - 48, MH - 10)
            sides.append((x0 + 22, b["y"] + b["h"] / 2, b["x"], b["y"] + b["h"] / 2, kind))
            y += MH - 10 + 10
    tx = x0 + 22
    for site, ms in zones:
        zy = y + 6
        y = zy + 30
        for mm in ms:
            b = _box(boxes, mm, "machine", x0 + 48, y, w - 48, BH)
            branches.append((tx, y + BH / 2, b["x"], bool(mm.get("up"))))
            y += BH + 10
        zlist.append({"label": zone_label(site), "x": int(x0 - 6), "y": int(zy), "w": int(w + 12), "h": int(y - 10 + 14 - zy)})
        y += 14
    last = branches[-1][1] if branches else 8 + MH
    return {"w": int(width), "h": int(y + 4), "boxes": boxes, "tunnels": [], "branches": branches, "sides": sides,
            "zones": zlist, "trunk": (tx, 8 + MH, last)}
