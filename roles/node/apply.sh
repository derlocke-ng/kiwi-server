# roles/node/apply.sh — deploy the rendered module stack.
# shellcheck shell=bash
# shellcheck disable=SC2154
ks_role_apply() {
    ks_stack_apply
    case ${KS_ROLE_PRESET:-} in
        gateway) ks_say "LAN clients: DNS = this machine; default gateway = this machine routes them through the VPN" ;;
        cloud)   ks_say "Nextcloud: open the admin URL above once to finish its setup" ;;
    esac
}
