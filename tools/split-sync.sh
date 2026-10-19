#!/usr/bin/env bash
# tools/split-sync.sh — kiwi-server is still the source of the renderer and
# of the machine side; this copies them into checkouts of kiwi-modules and
# kiwi-stack, or checks that the checkouts carry exactly what this tree has.
# The flip (kiwi-server vendoring both as subtrees pinned by tag) comes once
# each repository has a tagged release.
#
#   tools/split-sync.sh [--check] [KIWI_MODULES_DIR] [KIWI_STACK_DIR]
#   (defaults: ../kiwi-modules ../kiwi-stack)
set -euo pipefail
cd "$(dirname "$0")/.."
check=0
[[ ${1:-} == --check ]] && { check=1; shift; }
modules_dir=${1:-../kiwi-modules}
stack_dir=${2:-../kiwi-stack}
drift=0

# source:destination; a trailing slash on the source copies the tree
modules_map=(
    lib/kiwimodules/:lib/kiwimodules/
    modules/:modules/
    roles/common/stack.yaml:profiles/common/stack.yaml
    tests/golden/sh3/:tests/golden/sh3/
    LICENSE:LICENSE
)
for r in roles/*/role.yaml; do
    name=$(basename "$(dirname "$r")")
    modules_map+=("$r:profiles/$name/role.yaml")
done
stack_map=(
    roles/common/kiwi-stack:kiwi-stack
    roles/common/lib.sh:lib.sh
    LICENSE:LICENSE
)

sync_one() { # DEST_ROOT SRC:DST
    local root=$1 src=${2%%:*} dst=${2#*:}
    dst=$root/$dst
    if [[ $src == */ ]]; then
        if [[ $check -eq 1 ]]; then
            if ! diff -r -x __pycache__ -x '*.pyc' "${src%/}" "${dst%/}" >/dev/null 2>&1; then
                echo "drift: $src -> $dst" >&2
                diff -r -x __pycache__ -x '*.pyc' "${src%/}" "${dst%/}" 2>&1 | head -n 20 >&2 || true
                drift=1
            fi
        else
            rm -rf "${dst%/}"
            mkdir -p "$(dirname "${dst%/}")"
            cp -rp "${src%/}" "${dst%/}"
            find "${dst%/}" -name '__pycache__' -type d -prune -exec rm -rf {} +
        fi
    else
        if [[ $check -eq 1 ]]; then
            if ! cmp -s "$src" "$dst"; then
                echo "drift: $src -> $dst" >&2
                diff -u "$src" "$dst" 2>&1 | head -n 20 >&2 || true
                drift=1
            fi
        else
            mkdir -p "$(dirname "$dst")"
            cp -p "$src" "$dst"
        fi
    fi
}

for d in "$modules_dir" "$stack_dir"; do
    [[ -d $d ]] || { echo "error: $d is not a directory — clone the repository there, or give its path" >&2; exit 1; }
done
for m in "${modules_map[@]}"; do sync_one "$modules_dir" "$m"; done
for m in "${stack_map[@]}"; do sync_one "$stack_dir" "$m"; done

if [[ $check -eq 1 ]]; then
    [[ $drift -eq 0 ]] && echo "split-sync: kiwi-modules and kiwi-stack carry what this tree has"
    exit $drift
fi
echo "split-sync: copied into $modules_dir and $stack_dir — commit there"
