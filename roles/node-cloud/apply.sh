# roles/node-cloud/apply.sh — deploy the rendered module stack.
# shellcheck shell=bash
# shellcheck disable=SC2154
ks_role_apply() {
    ks_stack_apply
    ks_say "Nextcloud: open the admin URL above once to finish its setup (user admin, the cloud.admin_password setting)"
}
