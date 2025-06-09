FROM fedora:latest

# Install dependencies (including python3, python3-pip, python3-crypt-r, pyyaml)
RUN sudo dnf install -y \
    coreos-installer \
    butane \
    git \
    make \
    util-linux \
    curl \
    whois \
    openssl \
    python3 \
    python3-pip \
    python3-crypt-r \
    python3-pyyaml \
    && sudo dnf clean all

# Create a working directory
WORKDIR /workspace

# Copy the entrypoint script, main script, and Python config generator into the container
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY generate-coreos-iso.sh /usr/local/bin/generate-coreos-iso.sh
COPY generate-server-config.py /usr/local/bin/generate-server-config.py

# Make scripts executable
RUN sudo chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/generate-coreos-iso.sh /usr/local/bin/generate-server-config.py

# Set the entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["--help"]
