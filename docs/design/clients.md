# People's computers as clients (design, 28 Nov 2026)

Partly built: the agent reports (2.329.0); the Devices page shows each computer with the machine
whose hardware it runs on; the profile (CA, VPN, DNS mode), the two orders and the one-time join
command (next release). The unlock policy per client is designed, not built. What the owner asked for at the move (28 Nov): see which clients run, and push
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

## Two kinds of computer (the owner, 28 Nov 2026)

- **Personal**: its person is its admin (in `wheel` or `sudo`), as on the owner's desktops and
  pentest systems. The network keeps its VPN, CA, disk and updates; the person decides the rest.
- **Managed**: a company's computer. Its people log in with their own account and have no admin
  rights; the network decides what is installed (a browser, office, the company's tools) and how
  the desktop is set. `kind: managed` in the client's block.

## Accounts, as a domain does (managed computers)

What Windows does with its domain, and Fedora's world with FreeIPA: a person logs in to any of
the company's computers with one account, and the network says who may log in where and who is
an admin there.

- The accounts are the network's own: Kanidm, which the `accounts` module runs already. On a
  managed computer the agent sets up Kanidm's desktop part (`kanidm-unixd`: logins through PAM and
  NSS, kept for when the computer is away from the network).
- Kanidm's groups decide: members of the network's `admins` get sudo on its managed computers,
  everyone else does not; a group per computer or site says who may log in there.
- FreeIPA was looked at: the standard on Fedora (SSSD, sudo rules held centrally, Fleet Commander
  for GNOME), but it brings its own directory, Kerberos and a second CA beside the network's.
  Kanidm is in the network already.

## Policy, as group policy does (managed computers)

The profile carries the computer's policy, and the agent applies it as root, as it does the CA and
the VPN:

- **Software**: the apps it has (Flatpak: a browser, LibreOffice, …) and that nothing else can be
  installed by its people (polkit: installing is for admins only); the network's own apps through
  kiwi-updater's catalogs.
- **The desktop**: GNOME's settings, and the ones its people may not change (dconf keys and locks:
  screen lock, updates, extensions).
- **The browser**: the network's CA, the start page, allowed extensions (Firefox's and Chromium's
  policy files).
- The agent reports what differs from the policy; the Devices page shows it.

## Install media

`kiwi-server client iso FLEET NAME --system silverblue|bluefin|bluefin-dx`: Fedora's installer
with a kickstart that installs the chosen image straight from its registry (`ostreecontainer`:
Silverblue from quay.io/fedora, Bluefin and Bluefin-DX from ghcr.io/ublue-os), on an encrypted
disk. The person types the disk's passphrase at the install: it is never on the stick. The
stick carries a one-time join code, which the first start spends: the agent, the profile, for a
managed computer the accounts. The Silverblue drill (`tests/drills/desktop-silverblue-vm.sh`)
installs the same way already. NixOS later: the agent as a NixOS module.

## Where the parts live

- The agent (the script on the computer): kiwi-server's `clientagent.py`, served by the control
  node. It moves to [kiwi-agent](https://github.com/derlocke-ng/kiwi-agent) and comes back into
  kiwi-server vendored, as kiwi-stack does.
- The app on the computer, *Kiwi Agent*: [kiwi-agent](https://github.com/derlocke-ng/kiwi-agent),
  made from kiwi-template, installed and updated by kiwi-updater. It shows what the agent says
  and offers its admins Join, Bind, Update and Leave.

## Build order

1. ~~The agent and the join on the VMs of the desktop drills.~~ (2.329.0-2.330.1)
2. ~~The clients in the Devices table, with the box each one runs on.~~ (2.330.0)
3. ~~The profile: CA, VPN, DNS mode; the orders.~~ (2.330.0)
4. This desktop, Bluefin-DX on the workstation, one pentest SSD, the laptops.
5. ~~The unlock policy per client~~ (2.331.0), proven on the Debian VM.
6. The install media for Silverblue, Bluefin and Bluefin-DX.
7. Managed computers: the accounts, then the policy, on a Silverblue VM first.
8. The agent's script into kiwi-agent; the page's switch for a computer's auto-unlock.
