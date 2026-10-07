# roles/master/apply.sh — deploy the rendered module stack.
# shellcheck shell=bash
# shellcheck disable=SC2154
ks_role_apply() {
    ks_stack_apply
    ks_say "admin pages: wg.$KS_HOSTNAME and pihole.$KS_HOSTNAME from an admin group in the mesh; ssh -L 51821:127.0.0.1:51821 otherwise"
}
