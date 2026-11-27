# Using your network

Your machines run. This page is about the days after: getting your own devices
onto the network, opening its services, and keeping it well.

## Get a device onto the network

The services of your network open by name (`cloud.papaya.home`) and only from a
device that is on the network. There are three ways onto it.

**The computer you run Kiwi Server on.** In the app: "Connect this computer…" in
the menu (the overview offers *Connect* when the computer is not on the network). In a terminal:

```bash
kiwi-server connect ~/kiwi/fleet.yaml
```

The computer gets its own place on the VPN and a connection that carries only
your network. Everything else on it stays as it is.
[This computer on the network](network.md#this-computer-on-the-network) says
what it changes.

**A phone, a tablet, another laptop.** In the app: "Add a phone or laptop…".
On the control page: Devices. In a terminal:

```bash
kiwi-server enroll ~/kiwi/fleet.yaml phone --qr
```

Scan the code with the WireGuard app on the device. To show the code again later,
or to save it as an image, while the secret store is open
(`kiwi-server secrets unlock`, see [Security](security.md#the-secret-store)):

```bash
kiwi-server qr ~/kiwi/secrets/devices/phone.conf
kiwi-server qr ~/kiwi/secrets/devices/phone.conf --png > phone.png
```

**Everything at home at once.** A machine with the gateway preset is your home's
ad blocker and VPN gateway. A device that uses that machine's address as its DNS
server and as its gateway is on the network without any app, and reaches the
Internet through the VPN. Set the two addresses on the device, or in your
router's DHCP settings for the whole home.

## Open a service and log in

The app shows "Open a service" at the top of a machine's page, the control page
on its *Services* page. A click opens the service in your browser.

The first time, a service asks for a user name and a password. Kiwi Server made
them:

- the app: "Logins…" on the machine's page;
- the control page: the "Logins" card on the machine's page, "Show password";
- a terminal:

```bash
kiwi-server logins ~/kiwi/fleet.yaml
```

Some services have no login to show: Immich lets the first person who opens it
make the admin account, and in Vaultwarden everyone makes their own.

## Keep it well

| What | Where |
|---|---|
| Is everything running? | the app's overview, the control page's Home, `kiwi-server status <fleet>` |
| Changed a setting | "Apply" on the machine (app, page), `kiwi-server apply <fleet> <machine>` |
| Newer versions of the services | "Update everything" on the control page, `kiwi-server apply <fleet> <machine> --update` |
| Does every name answer after an update? | `kiwi-server check <fleet>`: every name of the network from the master (the sites of a machine without the VPN on that machine itself), and every public name from your computer as a stranger reaches it, its certificate checked |
| A copy of the network itself | "Fetch a copy" on the page's Backups, `kiwi-server backup <fleet>`; keep it away from your machines, with the recovery key |
| Bring data back | "Snapshots" in the app, "Bring something back" on the page; [Backups](backups.md) |

The recovery key you wrote down at the start and a copy of the network are what
brings everything back when the computer or the control node is lost.

What happens when something breaks, and whether you could bring it back
today: [When something breaks](recovery.md).
