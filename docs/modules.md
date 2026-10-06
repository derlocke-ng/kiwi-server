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
| `docker_subnet`, `docker_subnet_base`, `docker_gateway`, `mtu` | the stack network |
| `docker_dir`, `service_user`, `timezone` | the stack directory and its owner |
| `master_ip`, `mesh_subnet`, `mesh_via`, `domain` | the master's mesh address, the mesh, the container the host routes it through, the fleet's domain |
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

host_integration:
  sysctl: { net.ipv4.ip_forward: 1 }
  kernel_modules: [wireguard]
  disable_resolved_stub: true    # the dns module: free port 53 from systemd-resolved

urls:                            # printed when the role finished
  - "Vaultwarden: {{ vault_domain or 'https://vault.' ~ hostname }}"

nginx:                           # server blocks for the reverse proxy (see below)
  ...

settings:                        # the user-facing settings (see below)
  ...
```

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
    type: string                 # string (default) | text | int | bool | enum | file | list | map | secret
    label: URL
    help: "The full URL clients use. Empty: https://vault.<hostname>."
    default: ""
    required: false
    options: [a, b]              # enum only
    group: Advanced              # GUI grouping within the module
    targets: [debian]            # only meaningful on these targets
    placeholder: secrets/sh3.conf
    pattern: '[a-z0-9.-]+'       # string values must match this regular expression in full (empty passes)
```

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
      extra_directives: ["proxy_redirect off;"]
      extra_locations: [{ path: /api/, config: "proxy_pass http://x/api/;" }]
```

Every announced name goes into the fleet's DNS records at the host's mesh
address, so a Pi-hole anywhere in the fleet resolves it.

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

## SELinux

Fedora CoreOS and uCore run docker with SELinux enabled, so every bind mount
of a config file or data directory in the templates carries the `:z` label
(`ro,z` for read-only ones): docker relabels the host path so the container
may read it. Docker ignores the label on Debian. A mount of
`/var/run/docker.sock` or `/lib/modules` must never be labelled.

## Adding a module

Create `modules/<name>/module.yaml` and `docker-compose.yml.j2`, add the
module to a role's `modules:` (or to a host's `modules:` override), render a
host and read `output/<host>/<host>.stack/`. The unit tests render every
preset; add yours to `tests/test_kiwiserver.py` the same way.
`KIWI_SERVER_MODULES=/path/to/modules` points kiwi-server at another module
tree — a checkout of kiwi-v2, for instance.
