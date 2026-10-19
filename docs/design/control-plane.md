# The control plane

Agreed in October 2026. kiwi-server stops being only a desktop tool and
becomes a service that can run on a node, so a kiwi-network can be created
and managed without a Linux desktop, from a phone, and by more than one
person. Nothing here replaces the render-time model: machines stay
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

## API

FastAPI over the core, OpenAPI-described so the GTK app gets a generated
client. Resources: fleet, hosts, roles, modules, renders, ISOs, enrollments,
backups, workloads, users, audit. Long operations (render, build, apply,
backup) are jobs with streamed logs. The web pages are Jinja templates with
HTMX for the interactive parts: no JavaScript build toolchain on a node.

## Order

Bug round one, the split, port ranges and the backup key, then `serve` and
the control module, then join tokens and generic ISOs, then workloads and
the game plugin. See [../ROADMAP.md](../ROADMAP.md).
