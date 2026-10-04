# roles/bare/apply.sh — runs after the base system is up. Nothing to set up.
# shellcheck shell=bash
# shellcheck disable=SC2154
ks_role_apply() {
    if [[ ${#KS_ROLE_PACKAGES[@]} -gt 0 ]]; then
        ks_apt "${KS_ROLE_PACKAGES[@]}"
    fi
    ks_say "bare system — nothing else to do"
}
