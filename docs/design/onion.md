# Hidden services

Some services are for people who are not on the VPN and whom you do not know: a git
server, a relay, a page. A hidden service (a Tor onion service) gives such a service an
address anyone can open with Tor Browser, without a public name, an open port at home or
your address in anyone's log.

## Rules

- **Tor never leaves through a home connection.** The Tor process of a node shares its VPN
  client's network: everything it sends goes through the mesh to the master and leaves
  there. A master with a commercial VPN exit sends it through that exit; a master without
  one sends it from its own address at the hoster. Either way your internet provider at
  home sees WireGuard to your master, never Tor. In a network without a master a machine
  can still run hidden services when a commercial VPN is its exit (a `vpn-client` with
  `vpn_provider: mullvad`, `protonvpn`, … in its modules): Tor leaves there, and your
  provider sees WireGuard to that VPN. A machine with no VPN client runs no hidden service,
  unless its owner chooses Tor without a VPN (`onion: { without_vpn: true }`): then Tor
  leaves through the machine's own connection, and the provider can see that it is Tor.
  Behind a VPN is the default; without one is the owner's choice, said with a warning.
- **A hidden service is one site of the machine's reverse proxy**, named as everywhere
  else: `git.papaya`. Tor hands the connection to the proxy on a port that only the stack
  reaches; the proxy answers as that one site and nothing else of the machine.
- **The address comes from the fleet's seed**, not from the machine: `kiwi-server` knows it
  before the machine is set up, and a machine installed anew keeps it.
- **Open to everyone, or to named people.** With no names, anyone who has the address gets
  in (and meets the service's own login). With names, each gets a key (Tor's client
  authorization): without it the service cannot even be found. Keys come from the seed
  too, so a name can be shown its key again and taken away again.

## In the fleet file

```yaml
hosts:
  papaya:
    node:
      modules: [..., onion]
      onion:
        services:
          git: {}                       # everyone with the address
          cloud: { clients: [anna] }    # only who has anna's key
```

`kiwi-server onion <fleet>` lists them with their addresses, `kiwi-server onion <fleet>
git.papaya` makes one, `--client anna` limits it and prints what anna types into Tor
Browser, `--remove` takes a client or the service away. "Who may reach what" shows them
beside the groups and the linked networks.

## How it is built

- kiwi-modules, `onion`: a Tor container with `SocksPort 0`, one `HiddenServiceDir` per
  service, its keys and `authorized_clients` written at start from what kiwi-server
  rendered. `network_mode: service:<vpn-client>` on a node and on a master that has one.
- kiwi-modules, `reverse-proxy`: for a site with an address in `onion`, one more server on
  port 8043 (not published) under the `.onion` name, proxying as the site does and telling
  the service its usual name, so it does not redirect a stranger to a name only the VPN
  knows.
- kiwi-server, `onion.py`: the keys. Service key: Ed25519 from
  `HMAC-SHA256(seed, "onion/<host>/<service>")`; address: base32 of the public key, a
  checksum and the version. Client key: X25519 from `…/<service>/<client>`.

## Later, same mechanism

- **A service through the VPN**, as the download station already is: a switch on a module
  that puts its container into the VPN client's network, so what it fetches leaves at the
  master (its exit, or its own address when it has none). The hidden services are the
  first module after the downloader to rely on it; the switch for others is one line in
  their compose file and a setting.
- The emergency channel of [recovery.md](recovery.md): SSH and the control page as hidden
  services with named keys only.

## Mind

- A service open to everyone is open to everyone: keep its own sign-up off unless you
  want strangers to have accounts (`git: { registration: false }` is the default).
- Some services insist on their usual address in links they generate. Pages work; a
  link that names `https://git.papaya.home` does not open for a stranger.
