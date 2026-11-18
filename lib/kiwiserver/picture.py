"""Where everything stands in the picture of the network, as a family tree: the masters on top
(this network's master, its standbys beside it, the networks linked to it on the right), under
them the machines in a zone per site (at home, and every other place), each site's line coming
down from the master to a bar, a drop for each column and a short line into each machine. A
linked network's dotted line goes the way its traffic does: over the link into the master and beside the master's own line to each machine it may reach here. Pure geometry, no drawing:
the control page draws it as SVG (web.picture), the app with Cairo (NetworkPicture); both from
this, so that they show the same. The app loads this file by its path: it imports nothing.

    layout(master, machines, standbys=(), linked=(), width=860, narrow=False)

master, each standby: {name, state, cap}; each machine: {name, state, cap, up, site}; each linked
network: {name, cap, reach: [what it may reach here: cloud.papaya, lychee, papaya:22], theirs:
[what it opens to this network, as its DNS said; None: it did not say]}. What a linked network
opens stands in a zone of its own, its machines as boxes of kind `partner`.
Returns {w, h, boxes, lines, zones}:
  boxes  {name, state, cap, kind (master, standby, linked, machine, partner), up, site, net, reached: [linked
         networks that may reach it], hosts: [the machine it is], x, y, w, h}
  lines  {d: [["M", x, y], ["L", x, y], ["C", x1, y1, x2, y2, x, y]], kind, up, hosts, nets}
         kind: trunk (a site's line from the master), away (the same, to a site that is not
         home: over the Internet), bar, drop, stub (into one machine; up while it answers),
         standby, link (the master to a linked network), reach (a linked network to a machine),
         fed (in the zone of what a linked network opens: its line down, bar, drops and stubs)
  zones  {label, site, away, partner, x, y, w, h, lx (where the label starts, when not x + 14)}
"""

BW, BH, MW, MH, SW, PAD, ZGAP = 172, 54, 260, 58, 190, 10, 16
CW = BW + 30            # a column: its box and, left of it, the room for its drop
ROW = BH + 14           # a row: its boxes and the gap above the next (where a reach line runs)
TOP, BAR = 46, 34       # in a zone: where the first row starts, where the bar runs


def zone_label(site):
    return "At home" if site in ("", "home") else str(site)[:1].upper() + str(site)[1:] + " · over the Internet"


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


def opened(net):
    """What a linked network opens to this one, by machine: [(machine, [services])], services
    empty for the whole machine. git.damson, damson:22, damson -> damson."""
    out = {}
    for e in net.get("theirs") or []:
        e = str(e).split(":")[0].strip().lower()
        if not e:
            continue
        service, _, machine = e.partition(".") if "." in e else ("", "", e)
        out.setdefault(machine, [])
        if service and service not in out[machine]:
            out[machine].append(service)
    return sorted(out.items())


def _partner_zones(linked):
    """The zones of what the linked networks open: ("fed:" + name, [boxes to be])."""
    out = []
    for n in linked:
        found = opened(n)
        if found:
            out.append(("fed:" + n["name"], [{"name": m, "state": "", "cap": ", ".join(sv) if sv else "the whole machine",
                                              "net": n["name"], "up": None} for m, sv in found]))
    return out


def target(entry, names):
    """The machine a `share` entry is on: cloud.papaya, papaya, papaya:22 -> papaya; None when it
    names none of these."""
    e = str(entry).split(":")[0].strip().lower()
    if e in names:
        return e
    tail = e.split(".", 1)[1] if "." in e else ""
    return tail if tail in names else None


def _box(boxes, item, kind, x, y, w, h):
    boxes.append(dict(item, kind=kind, x=int(x), y=int(y), w=int(w), h=int(h), up=item.get("up"), net=item.get("net", ""),
                      reached=list(item.get("reached") or []),
                      hosts=[item["name"]] if kind in ("master", "standby", "machine") else []))
    return boxes[-1]


def _line(lines, d, kind, up=None, hosts=(), nets=()):
    lines.append({"d": [[p[0]] + [round(float(v), 1) for v in p[1:]] for p in d], "kind": kind, "up": up,
                  "hosts": list(hosts), "nets": list(nets)})


def _reached(machines, master, standbys, linked):
    """Each linked network's targets here, and for each machine the networks that reach it."""
    names = {m["name"] for m in machines} | {master["name"]} | {s["name"] for s in standbys}
    reach = {}
    for n in linked:
        reach[n["name"]] = sorted({t for t in (target(e, names) for e in n.get("reach") or []) if t})
    by = {}
    for net, ts in reach.items():
        for t in ts:
            by.setdefault(t, []).append(net)
    return reach, by


def fit_width(machines, linked=(), rows=3, least=860, most=1400):
    """A width at which no zone needs more than `rows` rows (for the page, whose picture scales to
    its place; the app lays out at the width it has)."""
    zones = _zones(list(machines)) + _partner_zones(list(linked))
    cols = sum(min(-(-len(ms) // rows), 8) for _s, ms in zones)
    return int(max(least, min(most, 2 * PAD + cols * CW + len(zones) * 14 + (len(zones) - 1) * ZGAP)))


def layout(master, machines, standbys=(), linked=(), width=860, narrow=False):
    standbys, linked = list(standbys), list(linked)
    reach, by = _reached(list(machines), master, standbys, linked)
    master = dict(master, reached=by.get(master["name"], []))
    standbys = [dict(s, reached=by.get(s["name"], [])) for s in standbys]
    machines = [dict(m, reached=by.get(m["name"], [])) for m in machines]
    zones = _zones(machines) + _partner_zones(linked)
    if not narrow:
        wide = _wide(master, zones, standbys, linked, reach, width)
        if wide:
            return wide
    return _narrow(master, zones, standbys, linked, min(width, 560) if not narrow else width)


def _top(master, standbys, linked, width, boxes, lines):
    """The masters' row: the standbys to the left, the linked networks to the right; on a row of
    their own under the master when one row is too narrow. Returns the master's box and the foot."""
    side = len(standbys) + len(linked)
    one_row = MW + side * (SW + 28) <= width - 2 * PAD
    if one_row:
        x = (width - (MW + side * (SW + 28))) / 2
        for s in standbys:
            _box(boxes, s, "standby", x, 10, SW, MH - 6)
            x += SW + 28
        m = _box(boxes, master, "master", x, 8, MW, MH)
        x += MW + 28
        for n in linked:
            _box(boxes, n, "linked", x, 10, SW, MH - 6)
            x += SW + 28
        for b in boxes:
            cy = b["y"] + b["h"] / 2
            if b["kind"] == "standby":
                _line(lines, [["M", b["x"] + b["w"], cy], ["L", m["x"], cy]], "standby", hosts=[b["name"], m["name"]])
            elif b["kind"] == "linked":
                _line(lines, [["M", m["x"] + m["w"], cy], ["L", b["x"], cy]], "link", hosts=[m["name"]], nets=[b["name"]])
        return m, 8 + MH
    m = _box(boxes, master, "master", (width - MW) / 2, 8, MW, MH)
    if not side:
        return m, 8 + MH
    sw = min(SW, (width - 2 * PAD - (side - 1) * 16) / side)
    x, y = (width - (side * sw + (side - 1) * 16)) / 2, 8 + MH + 22
    for kind, items in (("standby", standbys), ("linked", linked)):
        for s in items:
            b = _box(boxes, s, kind, x, y, sw, MH - 6)
            d = [["M", b["x"] + b["w"] / 2, b["y"]], ["L", m["x"] + m["w"] / 2, m["y"] + m["h"]]]
            if kind == "standby":
                _line(lines, d, "standby", hosts=[b["name"], m["name"]])
            else:
                _line(lines, d, "link", hosts=[m["name"]], nets=[b["name"]])
            x += sw + 16
    return m, y + MH - 6


def _columns(zones, columns):
    """How many columns each zone gets out of `columns`: one each, then one more where the most
    rows wait, until every zone has a row or less to spare or none are left."""
    cols = [1] * len(zones)
    extra = min(columns, 8) - len(zones)
    while extra > 0:
        rows = [-(-len(ms) // c) if c < len(ms) else 0 for (_s, ms), c in zip(zones, cols)]
        i = max(range(len(zones)), key=lambda k: rows[k])
        if rows[i] <= 1:
            break
        cols[i] += 1
        extra -= 1
    return cols


def _wide(master, zones, standbys, linked, reach, width):
    """The zones side by side, the machines in columns under a bar; None when not even one column
    for each zone fits at this width."""
    nz = len(zones)
    nets = [n["name"] for n in linked if reach.get(n["name"])]
    avail = width - 2 * PAD - (nz - 1) * ZGAP - nz * 14
    columns = int(avail // CW)
    if columns < nz:
        return None
    cols = _columns(zones, columns)
    boxes, lines, zlist = [], [], []
    m, foot = _top(master, standbys, linked, width, boxes, lines)
    zy = foot + 58
    widths = [c * CW + 14 for c in cols]
    x = PAD + (width - 2 * PAD - (sum(widths) + (nz - 1) * ZGAP)) / 2
    rows_max = max(-(-len(ms) // c) for (_s, ms), c in zip(zones, cols))
    zh = TOP + rows_max * ROW - 14 + 16
    by_name = {}
    ours = sum(1 for site, _ms in zones if not site.startswith("fed:"))
    route = {}          # a machine of ours: (trunk start x, its zone's middle, the bar's y, its drop's x, the zone's top)
    for k, ((site, ms), c, zw) in enumerate(zip(zones, cols, widths)):
        net = site[4:] if site.startswith("fed:") else ""
        away = not net and site not in ("", "home")
        hosts = [mm["name"] for mm in ms] if not net else []
        nets = [net] if net else []
        bar_y = zy + BAR
        drops = []
        for i, mm in enumerate(ms):
            r, j = divmod(i, c)
            bx, by_ = x + 30 + j * CW, zy + TOP + r * ROW
            b = _box(boxes, mm, "partner" if net else "machine", bx, by_, BW, BH)
            if not net:
                by_name[mm["name"]] = b
            _line(lines, [["M", bx - 14, by_ + BH / 2], ["L", bx, by_ + BH / 2]], "fed" if net else "stub",
                  None if net else bool(mm.get("up")), [] if net else [mm["name"]], nets or mm.get("reached") or [])
            if j == len(drops):
                drops.append([bx - 14, by_ + BH / 2, []])
            drops[j][1] = by_ + BH / 2
            drops[j][2].append(mm["name"])
        for dx, end, hs in drops:
            _line(lines, [["M", dx, bar_y], ["L", dx, end]], "fed" if net else "drop", hosts=[] if net else hs, nets=nets)
        tx = x + zw / 2
        if not net:
            for dx, _end, hs in drops:
                for h in hs:
                    route[h] = (m["x"] + 30 + (m["w"] - 60) * (k + 0.5) / max(ours, 1), tx, bar_y, dx, zy)
        left, right = min(drops[0][0], tx), max(drops[-1][0], tx)
        if right > left:
            _line(lines, [["M", left, bar_y], ["L", right, bar_y]], "fed" if net else "bar", hosts=hosts, nets=nets)
        if net:                         # down from the linked network's own box
            lb = next(b for b in boxes if b["kind"] == "linked" and b["name"] == net)
            sx, top = lb["x"] + lb["w"] / 2, lb["y"] + lb["h"]
        else:
            sx, top = m["x"] + 30 + (m["w"] - 60) * (k + 0.5) / max(ours, 1), m["y"] + m["h"]
        mid = (top + zy) / 2
        _line(lines, [["M", sx, top], ["C", sx, mid, tx, mid, tx, zy], ["L", tx, bar_y]],
              "fed" if net else "away" if away else "trunk", hosts=hosts + ([] if net else [m["name"]]), nets=nets)
        zlist.append({"label": net + " · opened to you" if net else zone_label(site), "site": site, "away": away, "partner": net,
                      "x": int(x), "y": int(zy), "w": int(zw), "h": int(zh)})
        x += zw + ZGAP
    # each linked network to what it may reach here, along the way its traffic goes: over the
    # link into the master, then beside the master's own line to the machine's site, along the
    # bar, down the machine's column and into it
    for k, net in enumerate(nets):
        lb = next(b for b in boxes if b["kind"] == "linked" and b["name"] == net)
        o = 4 + 4 * k
        cy = lb["y"] + lb["h"] / 2 + o
        for t in reach[net]:
            b = by_name.get(t)
            if b is None or t not in route:     # the master or a standby: the link reaches it already
                continue
            sx, tx, bar_y, dx, top = route[t]
            mid = (m["y"] + m["h"] + top) / 2
            ty = b["y"] + b["h"] / 2 - o
            _line(lines, [["M", lb["x"], cy], ["L", m["x"] + m["w"] - 24, cy], ["L", sx + o, m["y"] + m["h"] - 2],
                          ["L", sx + o, m["y"] + m["h"]], ["C", sx + o, mid, tx + o, mid, tx + o, top], ["L", tx + o, bar_y + o],
                          ["L", dx + o, bar_y + o], ["L", dx + o, ty], ["L", b["x"], ty]],
                  "reach", hosts=[t], nets=[net])
    return {"w": int(width), "h": int(zy + zh + 8), "boxes": boxes, "lines": lines, "zones": zlist}


def _narrow(master, zones, standbys, linked, width):
    """One under the other: the master, its standbys and linked networks (what each opens to this
    one right under it, indented), then each site with its machines; a line down from the master
    and a branch to each machine. The networks a machine is reached by are named on it
    (`reached`), without lines."""
    boxes, lines, zlist = [], [], []
    w = width - 2 * PAD
    x0 = PAD
    m = _box(boxes, master, "master", x0, 8, min(MW, w), MH)
    y = 8 + MH + 16
    tx = x0 + 22
    theirs = {site[4:]: ms for site, ms in zones if site.startswith("fed:")}
    zones = [(site, ms) for site, ms in zones if not site.startswith("fed:")]

    def turn(x, cy, to):
        # a quarter turn off a line down, as a cubic: the same in SVG and Cairo
        return [["M", x, cy - 10], ["C", x, cy - 3.3, x + 3.3, cy, x + 10, cy], ["L", to, cy]]
    for kind, items in (("standby", standbys), ("linked", linked)):
        for s in items:
            b = _box(boxes, s, kind, x0 + 48, y, w - 48, MH - 10)
            d = [["M", tx, b["y"] + b["h"] / 2], ["L", b["x"], b["y"] + b["h"] / 2]]
            if kind == "standby":
                _line(lines, d, "standby", hosts=[b["name"], m["name"]])
            else:
                _line(lines, d, "link", hosts=[m["name"]], nets=[b["name"]])
            y += MH - 10 + 10
            ms = theirs.get(s["name"]) if kind == "linked" else None
            if ms:                      # what it opens to this one: under it, its own line down
                net, px, zx = s["name"], b["x"] + 20, b["x"] - 6
                zy = y - 4
                y = zy + 30
                for mm in ms:
                    pb = _box(boxes, mm, "partner", px + 26, y, x0 + w - (px + 26), BH)
                    _line(lines, turn(px, y + BH / 2, pb["x"]), "fed", nets=[net])
                    last = y + BH / 2 - 10
                    y += BH + 10
                _line(lines, [["M", px, b["y"] + b["h"]], ["L", px, last]], "fed", nets=[net])
                zlist.append({"label": net + " · opened to you", "site": "fed:" + net, "away": False, "partner": net,
                              "x": int(zx), "y": int(zy), "w": int(x0 + w + 6 - zx), "h": int(y - 10 + 14 - zy),
                              "lx": int(px + 12)})          # the label right of the line down
                y += 18
    last, everyone = 8 + MH, [m["name"]]
    for site, ms in zones:
        away = site not in ("", "home")
        zy = y + 6
        y = zy + 30
        for mm in ms:
            b = _box(boxes, mm, "machine", x0 + 48, y, w - 48, BH)
            cy = y + BH / 2
            _line(lines, turn(tx, cy, b["x"]), "away" if away else "stub", bool(mm.get("up")), [mm["name"]], mm.get("reached") or [])
            everyone.append(mm["name"])
            last = cy - 10
            y += BH + 10
        zlist.append({"label": zone_label(site), "site": site, "away": away, "partner": "",
                      "x": int(x0 - 6), "y": int(zy), "w": int(w + 12), "h": int(y - 10 + 14 - zy)})
        y += 14
    _line(lines, [["M", tx, 8 + MH], ["L", tx, last]], "trunk", hosts=everyone)
    return {"w": int(width), "h": int(y + 4), "boxes": boxes, "lines": lines, "zones": zlist}
