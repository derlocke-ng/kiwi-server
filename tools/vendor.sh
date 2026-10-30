#!/usr/bin/env bash
# tools/vendor.sh — kiwi-server carries kiwi-modules (the renderer, the modules,
# the profiles) and kiwi-stack (the machine side) as vendored copies pinned by
# release tag in VENDORED. Development of those files happens in their own
# repositories; this tool brings a release in, or checks that the tree carries
# exactly the pinned releases. Submodules and pip do not survive the ostree
# desktops kiwi-updater installs onto, so the files are copied.
#
#   tools/vendor.sh                    fetch the tags in VENDORED and copy them in
#   tools/vendor.sh --check            fetch them and report every difference (CI)
#   tools/vendor.sh kiwi-modules=v0.3.0 [kiwi-stack=v0.3.0]
#                                      pin new releases in VENDORED, then copy them in
#
# A tag is taken from a checkout next to this one (../kiwi-modules, ../kiwi-stack)
# when it has the tag, from github.com otherwise.
set -euo pipefail
cd "$(dirname "$0")/.."
check=0
[[ ${1:-} == --check ]] && { check=1; shift; }
for arg in "$@"; do
    case $arg in
        kiwi-modules=*|kiwi-stack=*)
            name=${arg%%=*} tag=${arg#*=}
            if grep -q "^$name=" VENDORED; then sed -i "s|^$name=.*|$name=$tag|" VENDORED; else echo "$name=$tag" >> VENDORED; fi ;;
        *) echo "usage: tools/vendor.sh [--check] [kiwi-modules=TAG] [kiwi-stack=TAG]" >&2; exit 2 ;;
    esac
done
pin() { sed -n "s/^$1=//p" VENDORED | head -n 1; }
modules_tag=$(pin kiwi-modules); stack_tag=$(pin kiwi-stack)
[[ -n $modules_tag && -n $stack_tag ]] || { echo "error: VENDORED must name kiwi-modules=TAG and kiwi-stack=TAG" >&2; exit 1; }

work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
fetch() { # REPO TAG DEST — the release's tree
    local repo=$1 tag=$2 dest=$3 local_dir=../$1
    mkdir -p "$dest"
    if [[ -d $local_dir/.git ]] && git -C "$local_dir" rev-parse -q --verify "refs/tags/$tag" >/dev/null 2>&1; then
        git -C "$local_dir" archive --format=tar "$tag" | tar -x -C "$dest"
    else
        curl -fsSL "https://github.com/derlocke-ng/$repo/archive/refs/tags/$tag.tar.gz" | tar -xz -C "$dest" --strip-components=1
    fi
}
fetch kiwi-modules "$modules_tag" "$work/modules"
fetch kiwi-stack "$stack_tag" "$work/stack"

# release path : path in this tree; a trailing slash copies the tree
map=(
    "modules:lib/kiwimodules/:lib/kiwimodules/"
    "modules:modules/:modules/"
    "modules:profiles/common/stack.yaml:roles/common/stack.yaml"
    "modules:tests/golden/sh3/:tests/golden/sh3/"
    "stack:kiwi-stack:roles/common/kiwi-stack"
    "stack:lib.sh:roles/common/lib.sh"
)
for r in "$work"/modules/profiles/*/role.yaml; do
    name=$(basename "$(dirname "$r")")
    map+=("modules:profiles/$name/role.yaml:roles/$name/role.yaml")
done

drift=0
one() { # SRC_ROOT:SRC:DST
    local root=${1%%:*} rest=${1#*:} src dst
    src=$work/$root/${rest%%:*}; dst=${rest#*:}
    if [[ $src == */ ]]; then
        if [[ $check -eq 1 ]]; then
            if ! diff -r -x __pycache__ -x '*.pyc' "${src%/}" "${dst%/}" >/dev/null 2>&1; then
                echo "drift: $dst differs from the release" >&2
                diff -r -x __pycache__ -x '*.pyc' "${src%/}" "${dst%/}" 2>&1 | head -n 20 >&2 || true
                drift=1
            fi
        else
            rm -rf "${dst%/}"; mkdir -p "$(dirname "${dst%/}")"
            cp -rp "${src%/}" "${dst%/}"
            find "${dst%/}" -name '__pycache__' -type d -prune -exec rm -rf {} +
        fi
    else
        if [[ $check -eq 1 ]]; then
            if ! cmp -s "$src" "$dst"; then
                echo "drift: $dst differs from the release" >&2
                diff -u "$src" "$dst" 2>&1 | head -n 20 >&2 || true
                drift=1
            fi
        else
            mkdir -p "$(dirname "$dst")"; cp -p "$src" "$dst"
        fi
    fi
}
for m in "${map[@]}"; do one "$m"; done

if [[ $check -eq 1 ]]; then
    [[ $drift -eq 0 ]] && echo "vendor: the tree carries kiwi-modules $modules_tag and kiwi-stack $stack_tag"
    exit $drift
fi
echo "vendor: kiwi-modules $modules_tag and kiwi-stack $stack_tag copied in — review, run the tests, commit"
