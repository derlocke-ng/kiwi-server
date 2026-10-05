# The kiwi-server toolchain: the three build tools an ostree desktop does not
# ship. kiwi-server runs ONE tool per container invocation with the output and
# cache directories bind-mounted at the same paths, e.g.
#   podman run --rm -v "$OUT:$OUT:z" localhost/kiwi-server-toolchain butane --strict ...
# so the image needs no entrypoint and no scripts of its own.
FROM registry.fedoraproject.org/fedora:latest

RUN dnf -y install --setopt=install_weak_deps=False \
        butane coreos-installer xorriso cpio gzip curl gnupg2 util-linux \
    && dnf clean all

WORKDIR /work
