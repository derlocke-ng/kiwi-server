# The control plane

Agreed in October 2026, extended at the end of that month (the image, the
names, the unlock design, managed masters). kiwi-server stops being only a
desktop tool and becomes a service that can run on a node, so a kiwi-network
can be created and managed without a Linux desktop, from a phone, and by more
than one person. Nothing here replaces the render-time model: machines stay
reproducible from the fleet file; the control plane is the thing that holds
the fleet file and runs the renderer.

## Layers

1. **kiwi-modules**: the module definitions and the render contract for one
   machine. No fleet knowledge: DNS records, master address, CA, mesh subnet
   are explicit inputs.
2. **kiwi-stack**: the machine side. Quadlets or compose, the `kiwi-stack`
   CLI (start, stop, update, status, logs, backup, restore, health), the unit
   templates, `kiwi-stack apply DIR`. Installable from a tarball on any
   CoreOS or Debian box; no UI.
3. **kiwi-server core**: the Python library and CLI: fleet, hosts, targets,
   ISOs, CA, enroll, apply. Headless, as today.
4. **The control module**: the core packaged as a container with an API and
   a web UI, run as a module on a node like any other module. Its data dir
   is the fleet directory (fleet file, secrets, CA, output), which the fleet
   backup already ships to the other nodes.
5. **Clients**: the web UI (primary), the GTK app (stays; talks to the local
   CLI or to a controller over the mesh), the CLI. All logic lives in the
   core behind the API; a front end never grows logic of its own.

## Where it runs

On the home gateway node, reachable on the mesh for the admin group only,
on an encrypted disk once Tang lands. Not on the rented master: the
controller holds the CA key, every secret and SSH access to every node. The
master stays the always-on relay. The controller renders its own node like
any other; `apply` on its own host restarts the stack it lives in, so that
path restarts the control container last and must survive its own restart
(the role script runs outside the container).

The first machine is bootstrapped without a controller: by the desktop CLI
or GTK app, or later by the generic ISO's first-run page.

The node that runs the controller is *the control node* below. The rented
master is replaceable relay infrastructure: it holds what it needs to run
(the WireGuard server's key and peers, its own passwords, its leaf
certificate) and nothing that is the network (the seed, the CA key, the
fleet file, the nodes' configurations). Losing it costs a re-apply from the
control node; losing the control node costs a restore of the fleet backup.

## Users

A `users:` block in the fleet file with two roles:

- **fleet admin**: everything.
- **node admin**: bound to a list of hosts. Sees and manages those hosts'
  status, logs, workloads, backups and device enrollment into the groups the
  fleet admin allows; sees nothing of the master, the CA, other nodes or
  client groups.

Authorization is checked in the API, so every client obeys it. Login:
password plus the fleet CA client certificate or WebAuthn. Every change is
written to an audit log in the fleet directory.

## Setting up machines

Two paths, both kept:

- **Per-host ISO with embedded secrets** (today's path) for offline installs.
- **Generic bootstrap ISO per target**, built once by CI and published as a
  release artifact, carrying no secrets. On first boot it fetches its role
  from the controller with a one-time join token: `ignition.config.url` on
  CoreOS, `preseed/url` on Debian. A hand-installed machine or a remote VPS
  joins with `curl https://control.<host>.<tld>/join/<token> | sudo bash`.
  The controller renders on demand, so the token is the only thing a person
  types. The controller's image is the toolchain image (butane,
  coreos-installer, xorriso), so it also builds and serves per-host ISOs.

With the image (next section) the generic ISO is the stock Fedora CoreOS
live ISO plus a small Ignition that switches the installed system to the
kiwi image and fetches its role with the token. A VPS the provider installed
runs the join line from cloud-init user-data at creation, or by hand; where
a provider offers neither an ISO nor user-data, `coreos-installer` from its
rescue system installs the same thing. Before any apply to a machine someone
else installed, `kiwi-server probe <ssh target>` reports what it is (OS,
architecture, podman or docker and their versions, catatonit, whether sudo
asks for a password, disks, interfaces and routes, listening ports, running
containers, whether a DHCP server answers on its LAN) as JSON, and the fleet
file's `disk`, `pub_iface` and `address` are filled from it; `init` runs the
same probe locally for the LAN's subnet, gateway and resolvers. It is a
shell script, so it runs on CoreOS without Python.

## The image

One bootable container image (bootc), built and signed by CI in the
[phoenix-server](https://github.com/derlocke-ng/phoenix-server) repository, for every
machine kiwi-server sets up on the CoreOS lineage: plain uCore (or Fedora
CoreOS) plus `kiwi-stack`, `kiwimodules`, `dracut-sshd` for the unlock
fallback, and the toolchain the controller needs. **One image, not one per
role**: the role is a bundle, it arrives at first boot (from the user-data,
the join token or the per-host Ignition) and `kiwi-stack` applies it, so a
master and a node run the same image. Updates are `bootc upgrade` in the
machine's update window; the containers keep `AutoUpdate`. kiwi-server pins
the image by tag, as it will pin kiwi-stack and kiwi-modules. Debian stays a
target for machines that have to be Debian. The OS line is *phoenix*:
phoenix-server is this image, phoenix-desktop the Bluefin-based desktop
image that comes later (with a `kiwi-desktop` app, the Bluefin client of the
roadmap grown into managing several desktops through this API), phoenix-docs
their documentation.

The image gets onto a disk in three ways, all of them existing tools:

1. **The Fedora CoreOS live ISO with Ignition**, today's path. Ignition
   writes `/etc/containers/policy.json` and the cosign public key *before*
   the switch, so the installed system makes one signed `bootc switch` to
   the image and reboots once (uCore's autorebase needs the unsigned step
   only because the policy is not on disk yet). Ignition keeps everything
   it does today, the LUKS setup among it. `coreos-installer iso customize`
   does not touch the signed EFI binaries, so Secure Boot stays intact.
2. **bootc-image-builder** from the image itself: a raw or qcow2 disk image
   for providers that import one (the cleanest way to create a master), or
   an Anaconda ISO where Ignition is not wanted. No switch at all.
3. **From a running system**: `bootc install to-existing-root` on a
   provider's stock OS, or `coreos-installer` from a rescue system.

Two signatures, neither a burden: the *image* is signed with cosign in CI
and `policy.json` on every machine requires that signature; the key is kept
off every service, on a hardware token or offline, because whoever can sign
controls every machine that pulls. *Secure Boot* uses Fedora's shim and
kernel signatures, which the image keeps as long as it ships no kernel
modules of its own: the `-zfs` and `-nvidia` uCore variants carry ublue's
akmods and need ublue's MOK enrolled once at a console, so the image is
built on plain uCore or Fedora CoreOS and a VPS nobody ever sees a screen of
boots with Secure Boot on.

Considered and not adopted (October 2026): Ansible (Python on every target,
which CoreOS has not; a second source of truth beside the fleet file; heavy
to embed in the control module), Nix on Debian (packages, not the system),
NixOS (`nixos-anywhere` with `disko` is the one tool that installs a
declared system with LUKS onto a running machine over SSH, but it is a
third OS and a rewrite of the machine side; a `target: nixos` later at most).
pyinfra would fit the runtime half better than Ansible if an engine were
ever wanted; `kiwi-stack` is that engine.

## Workloads

Modules stay declarative and render-time. Dynamic workloads (game servers,
apps started from the UI) use the runtime path: podman's remote API over SSH
(`podman --remote --url ssh://user@node/run/podman/podman.sock`), docker's
`DOCKER_HOST=ssh://` likewise. The controller already has SSH to every node
for `apply`; no agent is needed. A small agent with mTLS from the fleet CA
can come later for streaming logs and events.

Containers created at runtime must not carry kiwi-stack's quadlet marker
(a re-apply removes marked units) and, if they are quadlets, must be
`PartOf=kiwi-stack.target` so stop and restart reach them. They are labelled
(`io.kiwi.workload=<kind>/<name>`) so status, backups and cleanup find them.

## The game plugin

kiwi-game is a workload plugin of the controller, not a separate UI: it
reuses authentication, the node connections, port and name reservations,
DNS and backups. It may live in its own repository that the controller
loads. It owns the game catalog (image, ports, data layout per game), idle
shutdown of unused servers, wake on first packet (a listener on the
reserved ports that starts the container and hands over), and its UI pages.

On the node, one `game` module reserves at render time what the plugin
needs, as module settings:

- a port range for games (tcp and udp), published on the host, opened in
  the host firewall and forwarded through the VPN client's DNAT so the mesh
  reaches them (the mesh address of the node);
- an address range on the module network that the renderer never assigns to
  module services, so DNAT can target game containers;
- the data directory and its backup paths;
- the name `games.<host>.<tld>` for the UI behind the reverse proxy;
- the podman or docker socket with the SELinux label disabled, as the
  portainer module does.

Game containers run on the module network with an address from the
reserved range and `restart` policies set by the plugin; they do not
auto-update (the catalog pins images by tag or digest).

## Names

Masters are named after fruit dishes (`crumble`, `sorbet`, `compote`,
`chutney`, `strudel`, `cobbler`, `trifle`, `cider`), nodes after fruits
(`papaya`, `lychee`, `guava`, `quince`, `damson`). A name is settable; the
default is a random, unused pick from two lists of DNS-safe single words, in
`init`, in the control plane when a tenant creates a node, in `enroll` for
a device without a name. The kind of a machine is readable in every log line.

## Unlock

Full disk encryption everywhere, with Tang and Clevis and nested Shamir
(HANDOFF, decisions to keep). This section is the design of the unlock,
agreed at the end of October 2026; it replaces the reverse SSH tunnel of the
earlier plan with a relay that is netfilter only.

**Threat model.** A thief takes the whole box, not the disk. FDE protects a
box that is off: the thing to make fail is a stolen box booting somewhere
else and unlocking by itself. A TPM alone cannot do that (the box boots, the
TPM hands over the key), so the fleet's own machines bind no TPM; `tpm2` is
an added pin for people who want "this hardware *and* the network agreed",
never a pin that unlocks alone. What distinguishes a legitimate reboot is
*where* it happens and *who* is present: a share that stays in the house, a
gate on where the request comes from, a share that exists only while the
owner's desktop is on. A box the thief keeps running is unlocked by
definition, as every server is; the *leash* below locks it again when it
leaves its house.

**Rings.** A Tang server is a *ring*: a policy with its own keys. Rings are
Tang containers (the `tang` module, one instance per ring), and a ring that
is closed is a container that is stopped: nothing listens, nothing to
firewall.

| ring | mode | binds it |
|---|---|---|
| `infra` | always serving, on the control node | nodes and masters: what must come back by itself |
| `desktops`, `desktops-2`, … | triggerable: stopped until the control module starts it for a window (a button on the phone page, an API call, presence of the owner's phone on the mesh) | the owner's or a household's desktops |
| `operator` | served by the owner's desktop through the kiwi-server app (`kiwi-server tang serve --ring operator`, a podman container, the port opened in the desktop's firewall); the desktop being on is the trigger | the control node |
| `emergency` | offline: keys generated once and exported to the owner (USB stick, printed, password manager); no machine serves them. In an emergency the owner starts `kiwi-server tang serve --ring emergency --keys /media/usb` anywhere, for a window | only the devices marked for it |

A ring unlocks only the devices that bound it, so opening `emergency` never
touches a device that was not marked for it, and a `desktops` window never
touches a node.

**Per device**, in the fleet file (and in the enrol bundle for a desktop):

```yaml
unlock:
  rings: [infra]          # the rings that may unlock it unattended
  local_tang: router      # the share at its site: the house test
  threshold: 2            # of the rings and local shares listed
  emergency: true         # also bound to the emergency ring
  tpm: false              # add a tpm2 pin (people who want one)
  leash: true             # reboot into the locked state when the local share is gone for 10 min
```

rendered into one Clevis SSS tree, for a node
`sss t=1 { sss t=2 {tang infra, tang router}, tang emergency, passphrase }`:
unattended needs two shares, the emergency ring alone works, a person with
the passphrase always can. For a desktop `sss t=1 { tang desktops, tang
emergency }`, with `tpm: true` as `sss t=2 {tpm2, tang desktops}`. The
**control node** cannot serve itself, so its shares are elsewhere:
`sss t=2 {tang router, tang operator, tang mirror}`, any two of the house's
router, the owner's desktop and a *mirror* of the `infra` ring on another
node (ring keys are files; two houses mirror each other's rings, which is
also what lets each house reboot while the other is down). Home with the
desktop on, or at night with the other house up: unattended. The box carried
off: router on the wall, desktop on the desk, mirror behind the gate: locked.

**Where a request goes.** The initramfs of a rebooting machine has network
and the stock `tang` pin, nothing else: no WireGuard, no SSH client, no
certificate, no key material on the unencrypted boot partition. Its URLs are
the house router and the master's public address.

- *The house share* is Tang on the router (OpenWrt has the package;
  `kiwi-server openwrt` writes it), or a second node in the house. It must be
  a different device that stays behind when the box leaves.
- *The control node's rings* sit on its mesh address through the VPN
  client's DNAT (`extra_dnat_rules: ["7500/tcp:kn-tang-infra:7500"]`, the
  mesh-SSH rule's pattern); the mesh reaches them, nothing dials into the
  control node.
- *The master relays* its public ring ports into the mesh with netfilter
  rules in the WireGuard server's namespace, where its firewall already
  lives and `wg show` is visible: `vpn-server.published_ports` publishes
  the ring ports, `unlock_relay:` (ring → port → mesh address) renders the
  DNAT over `wg0` with an SNAT to the master's mesh address, and the
  **endpoint gate** allows a ring port only from the recent WireGuard
  endpoint addresses of enrolled machines, rate-limited and logged: a node
  rebooting at home comes from the address it had a minute ago; a stolen box
  elsewhere is dropped before Tang hears of it. Mirrors are more rules on
  more ports. No userspace process: the packet never leaves the kernel on
  the master, and the master learns nothing it can use (Tang's exchange is
  safe in the clear and the LUKS header holds the server's thumbprint, so a
  relay can only make an unlock fail).
- *Not TLS.* Client certificates would authenticate the box, which is what
  was stolen, and would sit on the boot partition; Clevis's `tang` pin
  speaks plain HTTP by design. The fleet CA's mTLS belongs to the hops inside
  the running system: the agent and the heartbeat, the API, NFS with kernel
  TLS.

**Binding.** CoreOS: Ignition `boot_device.luks` with `clevis.tang` and
`threshold` for the simple trees, `clevis.custom` for the nested ones.
Debian: the preseed installs with a passphrase, the role script runs `clevis
luks bind … sss '<tree>'` and shreds the passphrase (ROADMAP, problem 15).
Desktops: through the Bluefin enrol bundle. Rotation is a controller job:
`tang` rotates a ring, then every device bound to it gets `clevis luks
regen` over SSH when it is up, queued until then; rotating `emergency` means
exporting the keys again.

**Fallback and tooling.** `dracut-sshd` in the image and `dropbear-initramfs`
on Debian keep an SSH server in the initramfs; `kiwi-server unlock <host>
--manual` connects (directly for a public master, on the LAN for a node at
home) to type the passphrase or forward a ring by hand. For a machine in
another house the fallback is simpler: the person there types the passphrase
at the console. The control module logs every unlock to the audit log and
pushes a notification; the `leash` is a host timer that checks the local
share's advertisement and reboots into the locked state when it has been
gone for ten minutes, optional per device because a router replacement must
not take a node down. Nodes: the stock pin; master: rules in `start.sh`;
control node: `tang` containers; desktop: `kiwi-server tang`. Nothing new in
the initramfs, which is the part that cannot be restarted to try again.

## Managed masters: the orchard

The owner, and later others, will run many masters for people who manage
their own nodes. That is a different product, and it shares nothing with a
tenant's network but the image and a heartbeat:

- **A tenant's network** is the control plane above: the control node at
  the tenant's home holds the seed, the CA, the fleet file and every
  configuration; its master is the rented relay. The host of the master
  cannot read the tenant's network, which is what makes hosting sellable and
  keeps the host out of the blast radius of a tenant's mistakes. Isolation
  is by VM, one master per tenant, no shared control plane across tenants.
- **The orchard** is a separate app or a kiwi-server plugin, a client of the
  API like the GTK app, written for anyone who runs several masters: it
  creates VPSes through provider APIs, hands each one its install (ISO,
  image import or rescue install) and a one-time registration token,
  receives a heartbeat from every master (version, health, disk, last
  update; mTLS with a certificate issued at provisioning), and controls
  rollouts by moving image tags per channel (`stable`, `canary`, a per-tenant
  pin). It cannot log into a master, read a fleet or touch a node. A tenant
  may send their encrypted fleet backup to the host's storage; the host
  holds ciphertext.
- **Federation and the orchard are two uses of one API**: masters that trust
  each other's CA and delegate names are clients of each other; the orchard
  is a client of many. The API is designed once for both, so the orchard
  comes almost free when federation lands, and it comes after it.

## API

FastAPI over the core, OpenAPI-described so the GTK app gets a generated
client. Resources: fleet, hosts, roles, modules, renders, ISOs, enrollments,
backups, workloads, users, audit. Long operations (render, build, apply,
backup) are jobs with streamed logs. The web pages are Jinja templates with
HTMX for the interactive parts: no JavaScript build toolchain on a node.

## Order

Bug round one, the split, port ranges and the backup key, then `serve` and
the control module, then the image and the join token, then workloads and
the game plugin; after that unlock, the standby master, federation and the
orchard. See [../ROADMAP.md](../ROADMAP.md).
