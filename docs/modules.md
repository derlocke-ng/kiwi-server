# Modules

A module is one service of a Kiwi Network machine: the VPN client, the
WireGuard server, Pi-hole, the reverse proxy, Nextcloud … A **role** picks a
set of modules (`master`, `node-gw`, `node-cloud`), kiwi-server renders them
for one host into a *stack* — a `docker-compose.yml`, the module config files,
the host units — and the host's role script unpacks and starts it on first
boot. The machine needs docker and nothing else; all templating happens on
the machine that runs `kiwi-server`.

The module format is kiwi-v2's (`kiwi-network-docker/kiwi-v2/modules`), with
the gaps its TODO listed filled in and a few keys added so a module can say
everything the generator needs. The modules shipped here are the live v1
stacks (kiwi-master, kiwi-node-gw, kiwi-cloud) taken apart.

## A module directory

```
modules/<name>/
├── module.yaml              what it is, what it needs, its settings
├── docker-compose.yml.j2    its services (merged into the host's compose file)
└── *.j2                     any config file it ships (nginx.conf, post-rules.txt, gw.sh, torrc …)
```

Templates are Jinja2. Every template sees the **host context**:

| variable | meaning |
|---|---|
| `hostname`, `node_type` | the host; `master` or `node` |
| `container_prefix`, `network_name` | `km` / `knet-master` on a master, `kn` / `knet-node` on a node |
| `container_ip`, `container_ips` | this module's address on the stack network, and everyone's |
| `docker_subnet`, `docker_subnet_base`, `docker_gateway`, `bridge_mtu` | the stack network |
| `wg_mtu` | the tunnels' MTU: gluetun's, wg-easy's `wg0` and the client configs it issues |
| `docker_dir`, `service_user`, `timezone` | the stack directory and its owner |
| `master_ip`, `mesh_subnet`, `mesh_via`, `domain` | the master's mesh address, the mesh, the container the host routes it through, the fleet's domain |
| `runtime` | `podman` (quadlets) or `docker` (compose) — the compose fragment is the source either way |
| `vpn_ip`, `pub_iface` | the host's mesh address and LAN interface |
| `proxy_ip`, `vpn_client_ip`, `dns_ip` | the reverse proxy's, VPN client's and Pi-hole's addresses (empty when absent) |
| `has_<module>` | `has_cloud`, `has_reverse_proxy` … for every enabled module |
| `module_config` | every enabled module's resolved settings, by module name |
| `dns_records`, `mesh_ips` | the fleet's `(address, name)` records and the mesh addresses in them |
| `enable_vpn_dnat`, `module_rules` | whether mesh traffic on 80/443 is forwarded to the proxy, and the iptables rules other modules add to the VPN client |
| `upstreams`, `servers` | the reverse proxy's aggregated blocks |
| `<file setting>_present` | `wireguard_config_present` — whether that file setting was given |

plus the module's own settings by key, and the lowercased `env.optional`
defaults of its module.yaml.

Addresses follow the v1 plan so a migrated machine keeps them: vpn-client
`.2`, vpn-server `.3`, dns `.4`, reverse-proxy `.5`, cloud `.6`, vault `.7`,
tor `.8`, downloader `.9`, gateway `.10`, portainer `.250`, sftp `.251`; any
other module counts up from `.100`. A host overrides one with
`container_ip:` in that module's block.

## module.yaml

```yaml
schema_version: "1.0"
module:
  name: vault                    # must equal the directory name
  title: Vaultwarden             # shown in the GUI
  description: One line.
  category: security             # core | network | productivity | security | storage | system
  node_types: [node]             # where it may run (default: both)

dependencies:
  required: [vpn-client, reverse-proxy]   # added automatically, rendered first
  optional: []

network:
  needs_vpn_dnat: true           # mesh traffic for the host's vpn_ip on 80/443 -> reverse proxy
  open_ports: ["{{ sftp_port }}/tcp"]     # what the HOST firewall must allow (templated)

firewall:
  scope: vpn-client              # vpn-client: the rules are collected into gluetun's post-rules.
  rules:                         # host / self: the module applies its own rules (gw.sh, start.sh);
    - "-t nat -A PREROUTING -d ${VPN_IP} -p tcp --dport {{ sftp_port }} -j DNAT --to-destination {{ container_ip }}:22"
                                 # rules: listed under those scopes are documentation, nothing reads them

templates:
  compose: docker-compose.yml.j2 # merged into the host's compose file
  torrc: torrc.j2                # any other key: rendered to outputs.<key>

outputs:                         # where rendered templates and file settings land
  torrc: { path: "{{ container_prefix }}-tor/etc/torrc", mode: 0644 }
  systemd_service: { kind: host_unit, name: kn-gateway.service }   # a unit on the host instead
  wireguard_config: { path: "{{ container_prefix }}-vpn-client/wg0.conf", mode: 0600 }
  refresh_timer: { kind: host_unit, name: "{{ container_prefix }}-x.timer", when: x_enabled }
                                 # when: only rendered while that context value is truthy

storage:
  volumes: [portainer_data]      # named volumes (the compose fragment may declare them too)
  bind_mounts:
    - { host: "${DOCKERDIR}/{{ container_prefix }}-vault", type: dir, required: true }
    - { host: "${DOCKERDIR}/{{ container_prefix }}-vault/x", type: dir, when: x_enabled }   # when: as above
    - { each: shared_folders, type: dir }   # one directory per value of a list or map setting

backup:                          # what the backup module keeps; a module without this block has nothing worth keeping
  frozen:                        # read with these containers paused: a database's files, a consistent copy without a dump
    containers: ["{{ container_prefix }}-db"]
    volumes: [app_db]
    paths: []
  paths:                         # read with everything running, however long that takes
    - "{{ data_path }}"
    - { each: shared_folders }
  volumes: []
  exclude: ["{{ data_path }}/thumbs", "appdata_*/preview"]   # absolute: a path on the host; else a restic pattern

host_integration:
  sysctl: { net.ipv4.ip_forward: 1 }
  kernel_modules: [wireguard]
  disable_resolved_stub: true    # the dns module: free port 53 from systemd-resolved

urls:                            # printed when the role finished
  - "Vaultwarden: {{ vault_domain or 'https://vault.' ~ hostname }}"

nginx:                           # server blocks for the reverse proxy (see below)
  ...

logins:                          # what a person signs in to (see below)
  ...

settings:                        # the user-facing settings (see below)
  ...
```

`validate:` lists checks over the module's context — `- { when: "collabora and
onlyoffice", error: "..." }` — that fail the render with that message. A
`secret` setting with `generate: true` is derived from the fleet's
`secrets/seed` when the fleet file leaves it empty, the same at every render.

Under `runtime: podman` the compose fragment is translated to quadlets: the
keys `image`, `container_name`, `environment`, `volumes`, `networks` with
`ipv4_address`, `network_mode` (`host`, `service:<name>`), `ports`, `cap_add`,
`cap_drop`, `sysctls`, `devices`, `restart`, `security_opt` (`label:disable`),
`init`, `read_only`, `tmpfs`, `user`, `shm_size`, `stop_grace_period`,
`hostname`, `command`, `depends_on` and `healthcheck`. Anything else is a
render error, so a module stays runnable on both runtimes. Images must be
fully qualified (`docker.io/...`): podman never guesses a registry.

`${VAR}` in module.yaml strings is the v2 convention and resolves against the
context (`${VPN_IP}` → `vpn_ip`, `${DOCKERDIR}` → `docker_dir`,
`${DOCKER_SUBNET2}` → `docker_subnet_base`); the string is Jinja-rendered
afterwards, so `{{ container_prefix }}` works too. An unset `${VAR}` is an
error, not an empty string.

### settings

The same schema roles use (`roles/<role>/role.yaml`). It validates the fleet
file and builds the GUI form:

```yaml
settings:
  - key: vault_domain            # snake_case; becomes {{ vault_domain }} in templates
    type: string                 # string (default) | text | int | bool | enum | file | list | map | object | secret
    label: URL
    help: "The full URL clients use. Empty: https://vault.<hostname>."
    default: ""
    required: false
    options: [a, b]              # enum only
    group: Advanced              # GUI grouping within the module
    targets: [debian]            # only meaningful on these targets
    placeholder: secrets/sh3.conf
    pattern: '[a-z0-9.-]+'       # string values must match this regular expression in full (empty passes)
    fallback_file: "secrets/{hostname}.conf"   # file settings: taken when it exists and the setting is empty
    generate: true               # secret settings: derived from secrets/seed when empty (see above)
```

An `object` setting takes a YAML mapping as it is (`vpn-server.groups`): the
template gets the dict and checks its shape itself.

`file` settings are paths relative to the fleet file; their content is
embedded into the role script and written to `outputs.<key>`. `secret`
settings are masked by `kiwi-server show`.

In the fleet file a host sets them under the role, then the module:

```yaml
hosts:
  sh3:
    role: node-cloud
    node-cloud:
      vpn_ip: 10.8.0.25                 # a role (stack) setting
      vault: { vault_domain: https://pw.sh3.home }   # a module setting
      modules: [vpn-client, reverse-proxy, vault]    # replaces the preset's list
```

A role preset can seed module settings with `module_defaults:` in its
role.yaml (the master sets `dns.network_mode: vpn-server`).

### derived, listen_ports

```yaml
derived:                         # name -> Jinja expression, available to every template of the module
  web_port_effective: "{{ web_port or (8081 if shares_namespace else 80) }}"
network:
  listen_ports: ["53/tcp", "53/udp", "{{ web_port_effective }}/tcp"]   # what the container binds inside its
                                 # network namespace; two modules sharing one (network_mode: service:X)
                                 # must not bind the same port — the renderer refuses, the machine would not say
```

### nginx

A module that wants to be served by the reverse proxy announces its blocks;
the reverse-proxy module (`nginx: { aggregator: true }`) renders them all into
one nginx.conf:

```yaml
nginx:
  upstream: { name: vaultwarden, server: "{{ container_prefix }}-vault:80" }   # optional
  server_name: "vault.{{ hostname }}"          # one block …
  proxy_pass: "http://vaultwarden"
  websocket: true
  servers:                                     # … or several
    - server_name: "{{ nc_hostname or 'cloud.' ~ hostname }}"
      proxy_pass: "http://nextcloud-aio-apache:11000"
      hsts: true
      client_max_body_size: "0"
      proxy_read_timeout: 86400s
      proxy_ssl: true                          # upstream speaks https (no verification)
      when: has_reverse_proxy                  # only when that context value is truthy
      site: cloud                              # a name for this site, for a login to point at
      extra_directives: ["proxy_redirect off;"]
      extra_locations: [{ path: /api/, config: "proxy_pass http://x/api/;" }]
```

Every announced name goes into the fleet's DNS records at the host's mesh
address, so a Pi-hole anywhere in the fleet resolves it.

### logins

A module that has something a person signs in to lists it here.
`kiwi-server logins`, the control page and the desktop app show the list with
each machine's own values
([Sign in to a service](machines.md#sign-in-to-a-service)):

```yaml
logins:
  - label: Downloads (JDownloader)   # what it is, in plain words (required)
    site: jd                         # which of the module's sites: the site: name of an nginx server.
                                     # Without it: the module's first site
    path: /admin                     # appended to the site
    address: "sftp://{{ hostname }}:{{ sftp_port }}"    # in place of site and path, for a service
                                     # the reverse proxy does not serve
    user: "{{ jd_web_user or transmission_user }}"      # the user name; left out when there is none
    password: [jd_web_password, transmission_password]  # the secret setting that holds it, or a list:
                                     # the first that is set. Left out when the network keeps no password
    note: "One plain sentence."      # for example how a person gets in when there is no password here
    when: jdownloader                # a setting, a derived value or has_<module>: listed only while it is set
```

`user`, `address` and `note` are Jinja over the module's settings. The address
of a site is `https://<site><path>`. A machine without the reverse proxy
serves no sites, so such a login has no address there.

The entries are checked when the modules are read. An unknown key, a
`password` that is not a secret setting of the module, a `site` that no nginx
server of the module has and a `when` that names nothing are errors.
`kiwi-server roles --porcelain` publishes the entries with each module, as
they are written.

## Rendering

`kiwi-server render` resolves the role's modules (dependencies first), builds
the context, renders every compose fragment, **parses it as YAML** and merges
the `services`, `volumes` and `networks` maps (a duplicate service name is an
error), then renders the other templates and file settings into the bundle.
The bundle also carries the directories to create (from `bind_mounts` and
every file's parent), the host ports to open, the sysctls, kernel modules and
host units. `docker compose config` validates the result when the plugin is
installed.

`output/<host>/<host>.stack/` holds the bundle for review; the role script
carries the same files and, on first boot, `ks_stack_apply` from
`roles/common/lib.sh`:

1. creates the service user, installs or enables docker, loads kernel
   modules, applies sysctls, turns off systemd-resolved's stub listener when
   the dns module is present;
2. unpacks the bundle into the stack directory (owned by the service user,
   compose file 0600), installs the host units;
3. installs `/usr/local/bin/kiwi-stack` (`start|stop|restart|update|status|logs|vpn-restart`)
   and `/etc/kiwi-server/stack.env`;
4. opens the ports in firewalld or ufw;
5. enables `kiwi-stack.service` (compose up at boot), the daily VPN restart
   and weekly update timers the stack settings ask for, and the host units.

## Rendering one machine without a fleet: kiwi-modules

The renderer is its own package, `lib/kiwimodules` (kiwi-server imports it;
`kiwiserver.modules`, `.quadlet` and `.schema` are aliases of it). It renders
a bundle from a one-machine file, with no fleet, no CA, no ISO:

```sh
python3 -m kiwimodules render sh3.yaml -o sh3.bundle   # files, host-units/, quadlets/, stack.sh
python3 -m kiwimodules modules -v                      # the modules and their settings
python3 -m kiwimodules profiles                        # master, node and its presets
scp -r sh3.bundle sh3:/tmp/ && ssh sh3 sudo kiwi-stack apply /tmp/sh3.bundle
```

```yaml
name: sh3                       # hostname: name.domain unless given
domain: home
target: ucore                   # coreos | ucore | debian — what the stack runs on
timezone: Europe/Berlin
admin: { user: core }           # the service user default
role: node                      # the profile: master, or node with a preset
node:
  preset: cloud
  vpn_ip: 10.8.0.25
  cloud: { admin_password: ... }
dns_records: [[10.8.0.1, gate.home], [10.8.0.25, sh3.home]]   # optional, the network's names
master_ip: 10.8.0.1             # optional: DNS upstream and mesh route
ca_cert: ca.pem                 # optional: the network's CA, trusted in the containers
seed: secrets/seed              # 32 random bytes; generated secrets derive from it
```

Paths are relative to the file; `secrets/<hostname>.conf` is the WireGuard
config fallback as in a fleet. The seed and the hostname decide every
generated secret, so the same machine file and seed render the same bundle
kiwi-server renders for that host (tests/test_golden.py checks this against
the golden sh3). Without a seed every secret-typed setting must be given.
`kiwi-stack apply DIR` on the machine (installed by any kiwi-server role
script, or from the kiwi-stack repository) does what the role script's
first boot does for the stack: directories, units, quadlets, ports, the
pull, the start and `kiwi-stack verify`.

The same package, modules and profiles are the
[kiwi-modules](https://github.com/derlocke-ng/kiwi-modules) repository, and
the machine side (`roles/common/lib.sh`, `roles/common/kiwi-stack`) the
[kiwi-stack](https://github.com/derlocke-ng/kiwi-stack) repository. Both are
developed there and released by tag; this tree carries them as vendored
copies pinned in `VENDORED`: `tools/vendor.sh` brings a release in,
`tools/vendor.sh --check` says whether the tree differs from its pins.

## What the renderer adds to every stack

- **Secrets stay out of the unit files.** A value of a `secret` setting never
  lands in a `.container` quadlet (readable through `systemctl show`): it goes
  into `kiwi/env/<service>.env`, root-only, referenced by `EnvironmentFile=`.
  The compose file keeps it inline and is 0600 itself.
- **The host's own names inside the containers** (`extra_hosts`, `AddHost=`)
  point at the reverse proxy's address on the stack network, not at the
  host's loopback (podman copies `/etc/hosts`) and not at the mesh address
  (a hairpin the VPN client rejects); Nextcloud reaches Collabora and Talk
  through its own URL this way.
- **The containers' resolver** (`dns`, `DNS=`) is this stack's Pi-hole when
  the dns module is on the host, Quad9 behind it, for every container on the
  stack network except Pi-hole and the VPN client. Without it podman would
  hand the containers a public resolver whenever the host's own is a loopback
  address.
- **The fleet CA** is written to `kiwi/ca/kiwiCA.crt` when the fleet has one;
  a module mounts it where its software expects CA certificates (the cloud
  module does, `ca_cert_dir` in the context).
- **Images** are listed for the role script (`KS_STACK_IMAGES`), which pulls
  them one by one with retries before the first start and then checks that
  every container runs (`kiwi-stack verify`); a stack that is not up fails
  the apply instead of being reported as applied.
- **Two MTUs, not one.** `wg_mtu` (1376) is the tunnels': gluetun's
  `WIREGUARD_MTU`, wg-easy's `WG_MTU` for the client configs, and the
  server's own `wg0`, which `start.sh` sets (left alone, wg-quick sizes it
  from the bridge). `bridge_mtu` (1500) is the stack network's, which carries
  the tunnels' *encrypted* packets — 60 bytes bigger over IPv4, 80 over IPv6.
  WireGuard also pads a packet to a multiple of 16, as far as the *sending*
  peer's MTU allows, so a peer with a bigger MTU (the commercial exit) grows
  a 1352-byte packet to 1360. `validate` refuses a `wg_mtu` whose padded
  packet plus 80 bytes does not fit `bridge_mtu` (at most 1408 under 1500).
  **Pick a multiple of 16 when the bridge or the uplink is tight**: it never
  grows. 1376 is one: 1436 bytes over IPv4 fits a 1452-byte DS-Lite path,
  1456 over IPv6 a 1460 one. The old single `mtu` setting is read as
  `wg_mtu`, with a warning, and refused when it does not fit (the usual 1412).
- **The MSS is set, not clamped to the path.** The WireGuard server's
  `start.sh`, and the VPN client's post-rules where `mss_clamp` is on (a
  master, and by default a node with the gateway module, whose LAN clients
  advertise MSS 1460), set every forwarded SYN to `wg_mtu - 40` with
  `--set-mss`, which never raises a smaller MSS. `--clamp-mss-to-pmtu` reads
  only the interface a SYN leaves by: on the v1 master the way back (wg0,
  1352) was wider than the exit tunnel (1344), and every connection's first
  full-size upload segment bounced.
- **Port ranges** are accepted as `A-B/proto` in `extra_dnat_rules`,
  `open_ports` and `ports:` (host firewall, DNAT and the collision check
  understand them; gluetun's own input port list takes single ports only).

## SELinux

Fedora CoreOS and uCore run podman (and docker under `runtime: docker`) with
SELinux enabled, so every bind mount of a config file or data directory in
the templates carries the `:z` label (`ro,z` for read-only ones): the runtime
relabels the host path so the container may read it. The label is ignored on
Debian. A mount of the podman or docker socket or of `/lib/modules` must never
be labelled: under podman the quadlet converter emits
`SecurityLabelDisable=true` for those containers.

## Adding a module

Create `modules/<name>/module.yaml` and `docker-compose.yml.j2`, add the
module to a role's `modules:` (or to a host's `modules:` override), render a
host and read `output/<host>/<host>.stack/`. The unit tests render every
preset; add yours to `tests/test_kiwiserver.py` the same way.
`KIWI_SERVER_MODULES=/path/to/modules` points kiwi-server at another module
tree — a checkout of kiwi-v2, for instance.
