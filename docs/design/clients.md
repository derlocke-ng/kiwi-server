# People's computers as clients (design, 28 Nov 2026)

Designed, not built. What the owner asked for at the move (28 Nov): see which clients run, and push
what they need. It builds on the servers' agent, the access manager, the unlock design
([unlock.md](unlock.md), "Desktops and laptops"), and [ui.md](ui.md) ("Desktops and laptops as
clients": managed through the control node's API, not by a second tool).

## What a client is

**The installed system, not the box.** This desktop's Bluefin, the workstation's Bluefin-DX, a
pentest system on a portable SSD, a laptop's Debian. Each has a key of its own, made when it
joins. A pentest SSD booted on the workstation shows as "pentest-1, on lemonaide's hardware";
booted on a laptop, the same client on another box.

**The box** is known by a sum of its board's id (`/sys/class/dmi/id/product_uuid`, read as
root), salted with the network's seed id, so the sum means nothing outside the network. The
servers' agent reports the same sum. The control node puts the two together: which system runs
on which hardware now.

## What its agent does

A script and a systemd timer, as on the servers. Nothing listens on the client.

- **Joins** with an invite link from the control page or the app, at home or over the VPN. The
  same kind of link that adds another computer or a phone. It gets its name, its key, the
  control node's address and the network's CA.
- **Reports** every minute, signed with its key: the box's sum, its system and version, how
  long it has run, whether its VPN is up and in which mode, whether its disk is bound to rings,
  its pending updates. Offline (away, no VPN), it reports when it is back.
- **Pulls its profile** from the control node and applies what changed: the CA in the system's
  trust store, its VPN profile in NetworkManager, the DNS mode (DNS only, or all traffic
  through the VPN; trusted networks with a kiwi gateway), its unlock policy. A profile is
  signed with the client's key, as orders are.
- **Takes orders**, as the servers' agent does: `update` (rpm-ostree on Bluefin and Silverblue,
  apt on Debian and Kali), `profile` (apply the profile now).

## Pentest systems

A pentest system is a client in a VPN group of its own (the access manager): out to the
Internet, and nothing at home except what is allowed for it. Its disk never opens by itself: no
auto-unlock ring, its passphrase only. A lost SSD: **Remove** on the page takes its VPN place
and its key away.

## On the page and in the app

The Devices table gets the clients: name, the box it runs on now, its system, last report,
VPN, updates. A client's page has **Update now** and **Apply its profile now**, both orders,
and **Remove**.

## Build order

1. The agent and the join on the VMs of the desktop drills (Debian GNOME, Silverblue on sh3).
2. The clients in the Devices table, with the box each one runs on.
3. The profile: CA, VPN, DNS mode. Then the orders.
4. This desktop, Bluefin-DX on the workstation, one pentest SSD, the laptops.
5. The unlock policy per client (unlock.md), proven in the same VMs first.

`kiwi-desktop`, an app on the computer (control-plane.md), comes after: a client of the same
API that shows the computer's state. The agent works without it.
