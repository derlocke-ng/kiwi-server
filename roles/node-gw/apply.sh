# roles/node-gw/apply.sh — deploy the rendered module stack.
# shellcheck shell=bash
# shellcheck disable=SC2154
ks_role_apply() {
    ks_stack_apply
    ks_say "LAN clients: DNS = this machine; default gateway = this machine routes them through the VPN"
}
