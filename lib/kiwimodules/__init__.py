"""kiwi-modules: the module stacks of a kiwi-network, rendered for one machine.

A module is a directory under modules/ (module.yaml + Jinja templates); a
profile is what a machine becomes (a master, a node with a preset); the
renderer turns one machine's settings into a bundle directory — a compose
file, podman quadlets, the module configs, the host units and a stack.sh
manifest — that `kiwi-stack apply DIR` puts on the machine. kiwi-server
embeds this package and adds the fleet: hosts, targets, ISOs, the CA, enroll.
"""
VERSION = "0.2.3"
