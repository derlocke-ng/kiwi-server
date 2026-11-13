# Install

This page is for everyone who wants to run kiwi-server on their own computer.
kiwi-server runs on the computer you manage the network from, not on the
machines it installs. It installs as your own user and needs no root rights.

## Requirements

| Needed for | Programs |
|---|---|
| Everything | Python 3.9 or newer with PyYAML and Jinja2, `openssl`, `ssh` |
| The secret store | `age` |
| Building ISOs | `butane`, `coreos-installer`, `xorriso`, and for Debian media also `cpio`, `gzip` and `curl` |
| The desktop app | python3-gobject, GTK 4 and libadwaita |
| Optional | `qrencode` (for `enroll --qr`), `shellcheck` (checks rendered scripts), `docker compose` (checks rendered stacks) |

`ssh` is needed because `apply`, `status`, `enroll` and the fleet backup reach
the machines over it. The ISO tools can run in a container instead; see
[The ISO toolchain](#the-iso-toolchain).

kiwi-server is tested in CI on Ubuntu (GitHub's `ubuntu-latest`). It is
written for Linux desktops such as Fedora Silverblue and Bluefin, where Python
and PyYAML are part of the base image.

## Install with kiwi

[kiwi-updater](https://kiwi-network.eu/docs/desktop/kiwi-updater/) installs
kiwi-server from the Kiwi catalog and keeps it up to date:

```bash
kiwi install kiwi-server
```

kiwi installs the newest version tag and updates it in the background. The
app appears with its icon in Kiwi Apps (`kiwi-gui`). To read the installer
before it runs, use `kiwi info --installer kiwi-server`. The Kiwi catalog is
registered when kiwi-updater is installed; if yours is missing, add it with
`kiwi catalog add https://github.com/derlocke-ng/kiwi-catalog.git`.

## Install from a checkout

The repository's `install.sh` is the same installer that kiwi runs:

```bash
git clone https://github.com/derlocke-ng/kiwi-server.git && cd kiwi-server
./install.sh install
kiwi-server doctor
```

The installer copies:

| What | Where |
|---|---|
| `kiwi-server` and `kiwi-server-gui` | `~/.local/bin` |
| the Python packages, roles, modules and examples | `~/.local/lib/kiwi-server` |
| the desktop entry, the icon and the bash completion | `~/.local/share` |

`KIWI_PREFIX` changes `~/.local`, and `XDG_DATA_HOME` changes
`~/.local/share`. The desktop app is installed only when Python can load GTK 4
and libadwaita; `KIWI_GUI=0` skips it. Without the app, the command-line tool
does everything.

If PyYAML or Jinja2 is missing, the installer warns and names the package:
`python3-pyyaml` and `python3-jinja2` on Fedora (`rpm-ostree install` on an
ostree desktop), or `pip install --user pyyaml jinja2`.

`./install.sh update` installs again over the existing copy.

## Check the installation

`kiwi-server doctor` checks everything kiwi-server uses: the Python version,
PyYAML and Jinja2, `openssl`, `ssh`, the roles and modules, a container
runtime, and each ISO tool, natively or in the toolchain image. It ends with
a list of what is missing. `kiwi-server version` prints the version.

## The ISO toolchain

Building ISOs needs `butane`, `coreos-installer` and `xorriso`:

| Tool | Used for |
|---|---|
| `butane` | turning Butane configs into Ignition (`--strict`) |
| `coreos-installer` | downloading and verifying the Fedora CoreOS ISO, `iso customize` |
| `xorriso`, `cpio`, `gzip`, `curl` | downloading and rebuilding the Debian netinst image |

On Fedora you can install them with `dnf install butane coreos-installer
xorriso`. On an ostree desktop such as Silverblue or Bluefin, do not layer
them onto the system. Build one container image with all of them instead:

```bash
kiwi-server toolchain build
```

kiwi-server then uses the image whenever a tool is not installed natively.
Inside the container, the tools see the output and cache directories at the
same paths, so nothing else changes. `kiwi-server toolchain` shows which
tools run natively and which in the container. `--toolchain native` or
`--toolchain container` on any command forces one of them.

Downloaded base images are cached in `~/.cache/kiwi-server`. `--cache DIR` or
the environment variable `KIWI_SERVER_CACHE` moves the cache.

## Uninstall

```bash
./install.sh uninstall           # or: kiwi uninstall kiwi-server
./install.sh uninstall --purge   # also the ISO cache and the toolchain image
```

Uninstalling removes the programs, the library, the desktop entry, the icon
and the completion. Without `--purge`, it keeps the ISO cache in
`~/.cache/kiwi-server` and the toolchain image `localhost/kiwi-server-toolchain`.
Your fleet files, `secrets/` and `output/` directories are never touched.
