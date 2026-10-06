"""kiwiserver — turn a small fleet.yaml into first-boot role scripts, Ignition
or preseed configs and unattended install ISOs for Kiwi Network machines.

Targets: Fedora CoreOS, uCore (CoreOS rebased onto a ublue image), Debian stable.
Roles:   bare, node-cloud (the kiwi-cloud stack), master — plus any directory
         dropped into roles/ that follows the same two-file convention.
"""
VERSION = "2.2.0"
