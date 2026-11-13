# Migrate a v1 machine

The first Kiwi Network setups (v1) ran their services with docker compose, in
data directories with older names. This page is for you if you want
kiwi-server to take over such a machine in place, keeping its data.

## Before the first start

1. Set `docker_subnet` in the host's role block to the subnet the v1 stack
   used.
2. Rename the data directories to the module names before the stack starts
   for the first time, for example `kmvpn-server` to `km-vpn-server` and
   `knvault` to `kn-vault`. wg-easy then keeps its peers, and the services keep
   their data.

The container addresses follow the v1 plan, so a migrated machine keeps them;
the [module reference](modules.md) lists them.

## podman or docker

The default runtime is podman. A machine that still runs the v1 docker
compose stack can either:

- keep docker, with `runtime: docker` in the role block, or
- have the old stack stopped before the role runs. The role script does not
  stop docker containers.

When you recreate containers by hand under docker, read the warning in
[Manage running machines](managing.md#on-the-machine-kiwi-stack).
