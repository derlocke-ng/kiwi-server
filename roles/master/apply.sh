# roles/master/apply.sh — deploy the rendered module stack.
# shellcheck shell=bash
# shellcheck disable=SC2154
ks_role_apply() {
    ks_stack_apply
    ks_say "clients: wg-easy at http://127.0.0.1:51821 on the master (ssh -L), Pi-hole admin at http://127.0.0.1:8080"
}
