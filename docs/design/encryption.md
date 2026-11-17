# Encrypted disks by default (design, 16 November 2026)

The rule: every machine's disk is encrypted. A machine without encryption only when its owner
chooses it, and then every place that shows the machine says so.

## What there is

- Rings (the `tang` module), `unlock:` per machine, the key for the console in
  `secrets/luks/<host>.key`, `kiwi-server unlock`, the `operator` ring on the computer with
  kiwi-server, the master's relay for machines behind NAT (docs/security.md, control-plane.md).
- The control node opens a machine that waits at boot by itself: it forwards a ring to it, at
  most twice a day (watchdog).
- The test network: every machine encrypted, written into the fleet by hand. Two power cuts:
  all back by themselves in about two minutes.

## What is missing

1. The first run, `add`, the page and the app write no `unlock:`. A new network is not
   encrypted unless someone edits the fleet file.
2. The starter stick installs CoreOS without LUKS, and a running CoreOS cannot be encrypted
   afterwards: taking such a machine in refuses `unlock:`.
3. Nothing says when a machine is not encrypted.

## Proposal

**Which rings, chosen by the first run and `add`:**

- The control node serves the ring `home` (the `tang` module goes on it). Every other machine at
  home binds `home`, with `ssh: true` for the console way in.
- The control node cannot open itself. It binds, as far as they exist:
  - `house` on a second machine at home (added when there is one);
  - `away` on the master, which answers only to the home's own address (the master's gate for
    rings, `vpn-server.unlock_relay`, already does this for rings at home). A stolen box
    elsewhere gets no answer;
  - `operator`, the computer with kiwi-server, while it is on.
  One of them is enough. A network with one machine at home and no master has none of them:
  after a power cut that machine waits for its key at its screen, and the first run says so.
- A master at a hoster binds `home` through a forwarded port, as sorbet does: the control node
  opens it after a reboot by itself. This replaces the decision of 10 November ("a master without
  LUKS"): its reason was that every reboot needed someone, and the control node now does it. A
  RAM master keeps nothing on its disk and stays as it is. CoreOS needs 4 GiB to encrypt its
  root, so a smaller VPS gets Debian.
- Debian machines get `debian.encrypt: true` as well.
- The order: a disk is bound when its rings answer. A machine whose rings do not exist yet (the
  master, installed before the control node) keeps its own key until the next apply after
  they do, and `kiwi-server next` does that apply.

**The starter stick installs encrypted.** CoreOS opens its root at boot only through Clevis,
and there is no ring yet when the stick installs. So the stick binds a stand-in. Taking the
machine in (or "Set it up" for the first one) adds the console key, binds the network's rings
and removes the stand-in, before anything secret reaches the disk. Debian's one-time key works
the same way today.

Tried in a local VM, 16–17 November (`tests/drills/standin-vm.sh`):

- Clevis's `null` pin: not possible. CoreOS's boot image has only the `tang`, `tpm2` and `sss`
  pins; Ignition fails.
- **The machine's TPM as the stand-in: works.** Installed with the root bound to the TPM, it
  came up by itself. Then, as a first apply does it (`unlock.bind`): the console key added from
  the TPM's slot, a ring bound, the TPM's slot removed, `rd.neednet=1`. After that it opened
  with the ring in 25 s, and with the console key when the ring was off. The TPM opens nothing
  once the machine is taken in, so the decision "no TPM" stands.
- A ring whose keys are thrown away, for a machine without a TPM: not possible. Ignition asks
  the ring at the first start even when it is given the ring's advertisement, and waits for it
  forever.
- **So a machine without a TPM 2.0** (most PCs since about 2016 have one, often switched off in
  the firmware as "fTPM" or "PTT") is not encrypted by the stick. The stick says so on its
  screen and page: switch the TPM on in the firmware and start again, or type PLAIN (as ERASE
  today) to install without encryption. Such a machine can be installed encrypted later with
  kiwi-server's own install stick for it, which binds the network's rings directly.
- CoreOS needs 4 GiB of memory to encrypt its root at the first start. With less, the same
  question as without a TPM.

**Without encryption only by choice:** PLAIN at the starter's screen (or its page's button).
`status` and the control page say for every such machine that its disk is not encrypted.

**Machines that run today without encryption** get the same warning. They become encrypted
only with a reinstall, which brings everything back from the backups once.

## In two steps

**First (2.159.0):** the starter stick encrypts with the TPM or asks for PLAIN; taking a machine
in and "Set it up" swap the TPM for the network's rings, or for the console key alone when the
network has no ring yet; every machine whose disk is not encrypted shows a warning (status,
the control page's overview and the machine's page). The starter stick is the way getting-started.md gives new
people, so a new network from it is encrypted.

**Second (2.161.0):** kiwi-server's own media for CoreOS and uCore encrypt too (rings when they
answer at build time, else the TPM until the first apply; the install checks for a TPM and 4 GB
first); the first run and `add` choose rings (the first machine at home with a fixed address
serves `home`, the others bind it); `encryption: false` keeps a machine plain by choice. Tried in
a local VM (`tests/drills/iso-encrypt-vm.sh`).

**Third (2.162.0):** the master is encrypted (Debian, as before on sorbet): the first run and
"Add a master" write `debian.encrypt` with SSH in its initramfs; its first start waits for the
one-time key of its media, and `kiwi-server next` sees that (its initramfs answers, its system
not) and gives it; the first apply puts its own key in place. After a restart the control node
types that key (the watchdog), as it forwards a ring to a machine that has one. Rings that do
not answer yet at an apply are bound at a later one; until then the disk opens with its key.
Tried in a local VM (`tests/drills/master-encrypt-vm.sh`).

**Then:** a ring for the first machine too (the master's, answering only the home's address),
and Debian machines at home.

## How it is tried

1. In a local VM: CoreOS with its root bound to the `null` pin boots, then a ring is bound and
   the stand-in removed; it reboots and opens with the ring, and with the console key when the
   ring is off. (This decides whether the stand-in works on CoreOS at all.)
2. `starter-vm.sh`: the stick installs encrypted; `alone` and the take-in leave it bound to
   the network's rings only.
3. A new test network from the first run: every machine encrypted, a power cut, all back by
   themselves.

## Data disks (2.167.0)

A new data disk (`mounts:` with a format word) goes into LUKS before its filesystem. Its key is
derived from the seed (`disk/<where>` of the machine), so a machine installed anew opens it
again, and is kept in `/etc/kiwi-server/disks/` on the system disk, which is encrypted itself:
`/etc/crypttab` opens the data disk after the system's. A disk with data on it is left as it is
and reported by `status`.
