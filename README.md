# Kiwi Server - CoreOS/uBlue Mass Deployment Tool

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Kiwi Server** is a KISS (Keep It Simple, Stupid) automation tool for mass deployment of Fedora CoreOS and uBlue systems. It generates customized ISOs and Ignition configs from a simple, human-friendly YAML configuration file, hiding the complexity of Butane while supporting all essential CoreOS features.

## 🚀 Features

### Configuration Management
- **Global + Per-Server Overrides**: Define defaults globally, override per server as needed
- **YAML Schema Validation**: Robust validation with helpful error messages
- **Config Merging**: Intelligent merging of global and per-server settings
- **Deduplication**: Automatic deduplication of files and settings

### User & Authentication
- **User Management**: Configure users, passwords, SSH keys, and groups
- **Password Hashing**: Automatic SHA-512 password hashing
- **SSH Key Management**: Global and per-server SSH key configuration

### Networking
- **DHCP/Static Configuration**: Support for both DHCP and static IP assignment
- **Auto IP Assignment**: Automatic static IP assignment from global IP ranges
- **Custom Subnet Masks**: Optional custom subnet mask overrides
- **NetworkManager Integration**: Generates proper NetworkManager connection files

### System Configuration
- **Systemd Services**: Enable services by simple names (docker, cockpit, etc.) or custom units
- **File & Directory Creation**: Create files and directories with custom permissions
- **Kernel Arguments**: Global and per-server kernel argument configuration
- **Hostname & MOTD**: Automatic hostname and MOTD setup

### Security & Encryption
- **LUKS Disk Encryption**: Full support for encrypted root and boot devices
- **Clevis Integration**: TPM2, Tang, and Shamir's Secret Sharing (SSS) support
- **Boot Device Encryption**: Specialized support for encrypted boot devices

### uBlue Integration
- **uCore Autorebase**: Automatic ucore autorebase systemd units and directory creation
- **Custom Images**: Support for custom uBlue images (global or per-server)
- **Signed/Unsigned Workflow**: Complete rebase workflow from unsigned to signed images

### Mass Deployment
- **Containerized Generation**: Reproducible builds using containerized toolchain
- **Batch Processing**: Process multiple servers in a single run
- **Output Organization**: Clean output structure with per-server directories
- **Error Handling**: Robust error handling with detailed feedback

## 📋 Prerequisites

- **Container Runtime**: Docker or Podman
- **Operating System**: Linux (tested on Fedora, should work on other distributions)
- **Network Access**: Internet connection for downloading base ISOs and container images

## 🛠️ Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/derlocke-ng/kiwi-server.git
   cd kiwi-server
   ```

2. **Make the main script executable**:
   ```bash
   chmod +x kiwi-server-gen.sh
   ```

3. **Build the container image**:
   ```bash
   ./kiwi-server-gen.sh build
   ```

## 🚀 Quick Start

1. **Copy the example configuration**:
   ```bash
   cp config-example.yaml config.yaml
   ```

2. **Edit your configuration**:
   ```bash
   nano config.yaml  # or your favorite editor
   ```

3. **Generate ISOs and configs**:
   ```bash
   ./kiwi-server-gen.sh generate config.yaml
   ```

4. **Find your generated files**:
   ```bash
   ls -la output/
   # output/server1/server1.bu   # Butane YAML config
   # output/server1/server1.ign  # Ignition JSON config  
   # output/server1/server1.iso  # Bootable ISO with embedded config
   ```

## 📖 Usage

### Basic Commands

```bash
# Build the container image
./kiwi-server-gen.sh build

# Generate ISOs from config
./kiwi-server-gen.sh generate config.yaml

# Generate with custom output directory
./kiwi-server-gen.sh generate config.yaml --output-dir /path/to/output

# Generate only configs (skip ISO creation)
./kiwi-server-gen.sh generate config.yaml --no-iso

# Force rebuild container before generation
./kiwi-server-gen.sh generate config.yaml --build

# Show help
./kiwi-server-gen.sh help
```

### Configuration File Structure

The configuration uses a simple two-level structure:

```yaml
global:
  # Global defaults for all servers
  user: core
  password: mypassword
  # ... other global settings

servers:
  server1:
    # Server-specific overrides
    hostname: server1
    # ... other server settings
  server2:
    hostname: server2
    # Inherits global settings unless overridden
```

## 📝 Configuration Reference

### Global Configuration Options

#### User Management
```yaml
global:
  user: core                    # Username (default: core)
  password: mypassword          # Plain text password (auto-hashed)
  password_hash: $6$...         # Pre-hashed password (alternative to password)
  ssh_keys:                     # SSH public keys
    - ssh-ed25519 AAAA...
    - ssh-rsa AAAA...
  groups: [wheel, docker]       # User groups
```

#### Networking
```yaml
global:
  network:
    interface: eth0             # Network interface name
    dhcp: false                 # Use DHCP (true) or static (false)
    gateway: 192.168.1.1        # Default gateway (static only)
    dns: [1.1.1.1, 8.8.8.8]   # DNS servers (static only)
    iprange: 192.168.0.10-192.168.1.100  # Auto IP assignment range
    mask: 24                    # Optional: override auto-calculated netmask
```

#### System Services
```yaml
global:
  services:                     # Services to enable (mapped to systemd units)
    - docker                    # → docker.socket
    - podman                    # → podman.socket
    - cockpit                   # → cockpit.socket
    - tailscale                 # → tailscaled.service
    - nfs                       # → nfs-server.service
    - samba                     # → smb.service
    - libvirtd                  # → libvirtd.socket
    - custom.service            # → custom.service (pass-through)
  
  systemd_units:                # Custom systemd units
    - name: my-service.service
      enabled: true
      contents: |
        [Unit]
        Description=My Service
        [Service]
        ExecStart=/usr/bin/my-command
        [Install]
        WantedBy=multi-user.target
```

#### Files and Directories
```yaml
global:
  files:
    - path: /etc/profile.d/hello.sh
      contents: 'echo Hello, world!'
      mode: 0755                # Optional: file permissions
      overwrite: true           # Optional: overwrite existing files
  
  directories:
    - path: /opt/mydir
      mode: 0755                # Optional: directory permissions
```

#### uBlue Configuration
```yaml
global:
  image: ghcr.io/ublue-os/ucore-hci:stable  # uBlue image for autorebase
```

#### Security and Encryption
```yaml
global:
  # LUKS encryption for additional devices
  luks:
    device: /dev/sdb
    name: encrypted_storage
    key_file: /etc/luks/key
    wipe_volume: true
    label: encrypted_storage
    mount_root: false           # Don't mount as root filesystem
    clevis:                     # Optional: Clevis integration
      tpm2: true
      tang:
        - url: https://tang.example.com
          thumbprint: ABCDEF123456
  
  # Boot device encryption
  boot_device:
    luks:
      tpm2: false
      tang:
        - url: https://tang1.example.com
          thumbprint: ABCDEF123456
        - url: https://tang2.example.com
          thumbprint: 123456ABCDEF
      # SSS (Shamir's Secret Sharing) example
      sss:
        threshold: 2
        tang:
          - url: https://tang1.example.com
            thumbprint: ABCDEF123456
          - url: https://tang2.example.com
            thumbprint: 123456ABCDEF
          - url: https://tang3.example.com
            thumbprint: FEDCBA654321
```

#### System Configuration
```yaml
global:
  hostname: default-hostname    # Default hostname
  motd: "Welcome to CoreOS!"    # Message of the day
  kernel_arguments:             # Kernel boot arguments
    - quiet
    - loglevel=3
    - myarg=value
  # timezone: UTC               # Not supported in Butane 1.6.0
```

### Per-Server Configuration

Any global setting can be overridden per server:

```yaml
servers:
  server1:
    hostname: server1
    network:
      address: 192.168.1.100/24 # Manual static IP (overrides auto-assignment)
      dhcp: false
      mask: 24                  # Override netmask for this server
    image: ghcr.io/ublue-os/ucore-hci:testing  # Different image
    ssh_keys:                   # Additional SSH keys (merged with global)
      - ssh-ed25519 AAAA...server1-specific-key
    services: [docker]          # Different service set
    files:                      # Server-specific files
      - path: /etc/server-id
        contents: server1
    kernel_arguments:           # Additional kernel args
      - server=1
  
  server2:
    hostname: server2
    network:
      dhcp: true                # Use DHCP for this server
    # All other settings inherited from global
```

## 🏗️ Architecture

### Components

1. **`kiwi-server-gen.sh`**: Main entrypoint script
   - Handles command-line interface
   - Manages container building and execution
   - Supports both Docker and Podman

2. **`container/generate-server-config.py`**: Configuration processor
   - Merges global and per-server configurations
   - Validates YAML structure and settings
   - Generates Butane YAML configurations
   - Handles IP range assignment and deduplication

3. **`container/generate-coreos-iso.sh`**: ISO generation engine
   - Converts Butane YAML to Ignition JSON
   - Downloads latest Fedora CoreOS ISOs
   - Embeds Ignition configs into ISOs
   - Manages caching and batch processing

4. **`container/entrypoint.sh`**: Container entrypoint
   - Provides argument parsing and validation
   - Bridges host and container environments

5. **`container/Dockerfile`**: Build environment
   - Based on latest Fedora
   - Includes all required tools: `coreos-installer`, `butane`, Python 3
   - Self-contained with no external dependencies

### Workflow

1. **Configuration Processing**: YAML config is validated and merged
2. **Butane Generation**: Per-server Butane YAML files are created
3. **Ignition Conversion**: Butane converts YAML to Ignition JSON
4. **ISO Generation**: Ignition configs are embedded into bootable ISOs
5. **Output Organization**: Files are organized in per-server directories

### Output Structure

```
output/
├── fedora-coreos-<version>-live.x86_64.iso  # Cached base ISO
├── server1/
│   ├── server1.bu     # Butane YAML configuration
│   ├── server1.ign    # Ignition JSON configuration
│   └── server1.iso    # Bootable ISO with embedded config
├── server2/
│   ├── server2.bu
│   ├── server2.ign
│   └── server2.iso
└── ...
```

## 🔧 Advanced Usage

### IP Range Assignment

Kiwi Server can automatically assign static IP addresses from a defined range:

```yaml
global:
  network:
    iprange: 192.168.0.10-192.168.1.100
    gateway: 192.168.1.1
    dhcp: false

servers:
  server1: {}          # Will get 192.168.0.10/24
  server2: {}          # Will get 192.168.0.11/24
  server3:
    network:
      address: 192.168.2.100/24  # Manual override
  server4: {}          # Will get 192.168.0.12/24 (skips manually assigned)
```

### Service Mapping

Common services are automatically mapped to their correct systemd units:

| Service Name | Systemd Unit |
|-------------|-------------|
| `docker` | `docker.socket` |
| `podman` | `podman.socket` |
| `cockpit` | `cockpit.socket` |
| `tailscale` | `tailscaled.service` |
| `nfs` | `nfs-server.service` |
| `samba` | `smb.service` |
| `libvirtd` | `libvirtd.socket` |

Other service names are passed through as-is.

### uCore Autorebase

Kiwi Server automatically sets up the ucore autorebase workflow:

1. Creates `/etc/ucore-autorebase` directory
2. Installs `ucore-unsigned-autorebase.service` for initial rebase to unsigned image
3. Installs `ucore-signed-autorebase.service` for subsequent rebase to signed image
4. Uses the `image` setting from your configuration

### LUKS Encryption Examples

#### TPM2 Boot Device Encryption
```yaml
global:
  boot_device:
    luks:
      tpm2: true
```

#### Tang Server Boot Device Encryption
```yaml
global:
  boot_device:
    luks:
      tang:
        - url: https://tang.example.com
          thumbprint: ABCDEF123456
```

#### Shamir's Secret Sharing (SSS)
```yaml
global:
  boot_device:
    luks:
      sss:
        threshold: 2
        tang:
          - url: https://tang1.example.com
            thumbprint: ABCDEF123456
          - url: https://tang2.example.com
            thumbprint: 123456ABCDEF
          - url: https://tang3.example.com
            thumbprint: FEDCBA654321
```

## 🐛 Troubleshooting

### Common Issues

**"Container build failed"**
- Ensure Docker or Podman is installed and running
- Check internet connectivity for package downloads

**"Config file not found"**
- Verify the config file path is correct
- Ensure the file has proper YAML syntax

**"No servers found in config"**
- Check that your config has a `servers:` section
- Verify YAML indentation is correct

**"Failed to download base ISO"**
- Check internet connectivity
- Verify DNS resolution is working

**"Butane validation failed"**
- Check the generated `.bu` file for syntax errors
- Ensure all required fields are present

### Debug Mode

To debug issues, you can inspect the generated Butane files:

```bash
# Generate configs only (no ISO)
./kiwi-server-gen.sh generate config.yaml --no-iso

# Check generated Butane config
cat output/server1/server1.bu

# Validate Butane config manually
butane --strict output/server1/server1.bu
```

### Container Runtime Issues

If you encounter SELinux issues with Podman:

```bash
# Check SELinux status
getenforce

# Use Docker instead of Podman
export CONTAINER_CMD=docker
./kiwi-server-gen.sh generate config.yaml
```

## 🤝 Contributing

Contributions are welcome! Please feel free to:

1. **Report Bugs**: Open an issue describing the problem
2. **Request Features**: Suggest new functionality
3. **Submit Pull Requests**: Contribute code improvements
4. **Improve Documentation**: Help make the docs clearer

### Development Setup

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test with your own configurations
5. Submit a pull request

### Code Style

- Shell scripts: Follow existing conventions, use `shellcheck`
- Python: Follow PEP 8, use type hints where appropriate
- YAML: Use 2-space indentation, maintain readability

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **Fedora CoreOS Team**: For the excellent CoreOS distribution and tooling
- **uBlue Project**: For the innovative universal blue approach
- **Butane Project**: For the human-friendly configuration format
- **Tang/Clevis**: For robust network-bound disk encryption

## 📞 Support

- **GitHub Issues**: For bug reports and feature requests
- **Discussions**: For questions and community support
- **Documentation**: Check this README and example configurations

---

Made with 🥝 for security enthusiasts
