# Who opens whom (design, 19 November 2026)

A machine with an encrypted disk opens at boot by itself, with the rings it names, or with a
person who gives it its key. The fleet file says this per machine today, but nothing shows the
whole picture, auto-unlock cannot be switched off without editing the fleet, and a master at a
hoster has one opener only: the control node. Desktops and laptops come later and need more
than "always by itself". This design is the one place for all of it.

## What there is

- **Rings** (the `tang` module): a ring holds half of a key and answers anyone who reaches it.
  A machine's `unlock.rings` names the rings it opens with, `unlock.threshold` how many of them
  it needs. The other half stays on its own disk. Its console key is in
  `secrets/luks/<host>.key`.
- A master at a hoster reaches no ring at boot: the control node opens a tunnel into its boot
  system (`unlock.via`, the watchdog). Machines at another site ask through the master (relay).
- The control node's own keys open with `unlock.control`, `control_threshold`.
- `rotate ring NAME`, `unlock --manual`, the leash, the recovery report's power-cut check.

## How a machine opens: one setting, three ways

`unlock.opens` per machine, a choice in the Disk unlock group of its settings:

| Way | What happens at boot | For |
|---|---|---|
| `by-itself` (built) | its rings open it, N of them | servers |
| `by-a-person` | it waits: its key at its screen, `unlock --manual`, or **Open now** on the page or in the app | a machine that should never open alone |
| `at-home` | its rings on its own LAN open it; anywhere else it asks for its passphrase | laptops, desktops (below: their policy) |

- Switching the way is an apply: it binds or unbinds the rings (unbinding exists since 2.234).
  `by-a-person` is "auto-unlock off". **Built (2.307.0)**: `by-itself` and `by-a-person`, switched on
  the page's Disk unlock and in the app's (a switch per machine, then an apply of it), or in the
  machine's settings. **The window (2.307.1)**: with auto-unlock off, **Opens by itself for N
  minutes** (`unlock.window`, 5 by default; `kiwi-server unlock FLEET HOST --window`, the action
  `window`) lets the control node's watchdog type its key if it waits at boot within those
  minutes; outside them the watchdog leaves it alone (before, `recovery.unlock` opened any waiting
  machine). Needs `unlock.ssh`. Next: `at-home`, then the desktops below.
- A ring answers anyone who reaches it, so a machine that is **off or stolen** cannot be held
  by a switch: the answer there is `rotate ring NAME`, after which its old binding opens
  nothing. The page says so beside the switch.
- `at-home` never uses a relay or an opener, and suits the leash.

## Openers: who opens the way for a machine away from home

- A machine that reaches its rings only through a tunnel (`unlock.via`, a master at a hoster)
  has **openers**: the control node, and each ring's own machine for its own ring.
  `unlock.openers` names them; by default the machines of its rings.
- An opener checks once a minute whether the machine waits in its boot system (Debian: port
  2222 answers with the host key on record; it is closed while the system runs). Then it opens
  the tunnel for its ring itself.
- Each opener has its own key, `secrets/openers/<opener>_ed25519`. Its line in the waiting
  machine's boot system allows nothing but offering a port: `no-pty`, no agent, no X11, a
  forced command that only waits (dropbear 2025.89 has no rule for one remote port; local
  forwarding is off for every key, `-j`). No shell, no password prompt.
- The private key lies on the opener's disk, so **only a machine with an encrypted disk is an
  opener**; kiwi-server refuses one without.

## One picture, in three places

The same table, from one function (`unlock.picture`), for the CLI, the page and both apps:

```
kiwi-server unlock fleet.yaml          # no machine: who opens whom
```

| Machine | Way | Opens with | Openers | Without one of them |
|---|---|---|---|---|
| sorbet | by itself | 1 of infra (papaya), home (lime) | papaya, lime | opens |

- Page: a **Disk unlock** page with that table, per machine its way (changeable), **Open
  now** for one that waits, **New keys** for a ring. API: `GET /api/unlock`, used by the
  Android app as well.
- The recovery report keeps its checks; they read the same table.

## Desktops and laptops (the owner, 19 November 2026)

A desktop's disk opens by a policy of rings (clevis `sss`), and its own passphrase always works:

```
opens = (every health ring  and  its auto-unlock ring)  or  its emergency ring
```

- **Health rings**: rings on the machines that must be up for the desktop to boot ("everything
  runs"). A missing one means something at home is wrong: the desktop waits.
- **Auto-unlock ring**: one per desktop, on the control node, running only while **Opens by
  itself** is on for it. Off: the desktop waits for its passphrase (or the button below).
- **Emergency ring**: one per desktop, on the control node, running only for a few minutes after
  **Unlock now** on the page or in the app. A waiting desktop asks its rings again every few
  seconds, so the button opens it within seconds, whatever else is down.
- A ring answers anyone who reaches it, so the switch and the button are rings that run or do not.
  The control node holds their keys on its encrypted disk.
- Away from home a laptop reaches no ring and asks for its passphrase, as before.
- Proven in VMs first: a Debian desktop (GNOME) and Fedora Silverblue, each bound with that
  policy to four test rings (`tests/drills/desktop-clevis-vm.sh`, `desktop-silverblue-vm.sh`):
  everything up opens it; auto-unlock off or a health ring down makes it wait; **Unlock now**
  opens it then; with no ring the passphrase does.
- No TPM (decided earlier). A lost laptop: **New keys** for its rings on the page.

## lime: an encrypted Armbian image

Armbian's build framework can build an image with an encrypted root (`CRYPTROOT_ENABLE`,
dropbear in the boot system). kiwi-server would build the image per board instead of
downloading it, in a VM (it needs root for loop devices), and write the first boot in as now.
The option has had bugs on some boards: it needs a try on lime first. Until then lime is not
encrypted and is no opener.

## Order

1. The picture: CLI, API, page, both apps. Read only.
2. `unlock.opens`, with **Open now**.
3. Openers.
4. The encrypted Armbian image, tried on lime.
