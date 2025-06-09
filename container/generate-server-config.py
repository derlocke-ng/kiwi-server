#!/usr/bin/env python3
import sys
import yaml
import argparse
from crypt_r import crypt, mksalt, METHOD_SHA512
import ipaddress

def merge_configs(global_cfg, server_cfg):
    merged = global_cfg.copy()
    for k, v in server_cfg.items():
        if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    # Ensure boot_device.luks is merged correctly if present in global or server
    if 'boot_device' in global_cfg or 'boot_device' in server_cfg:
        merged.setdefault('boot_device', {})
        if 'boot_device' in global_cfg:
            merged['boot_device'].update(global_cfg['boot_device'])
        if 'boot_device' in server_cfg:
            merged['boot_device'].update(server_cfg['boot_device'])
    return merged

def dedup_files(files):
    # Deduplicate by path, last one wins (server overrides global)
    seen = {}
    for f in files:
        seen[f['path']] = f
    return list(seen.values())

def get_ip_range(iprange_str):
    # Accepts e.g. 192.168.0.10-192.168.1.100
    if '-' in iprange_str:
        start, end = iprange_str.split('-')
        start_ip = ipaddress.IPv4Address(start.strip())
        end_ip = ipaddress.IPv4Address(end.strip())
        # Return all IPs in range (inclusive)
        return [str(ipaddress.IPv4Address(ip)) for ip in range(int(start_ip), int(end_ip)+1)]
    return []

def assign_ip_from_range(iprange, used_ips):
    for ip in iprange:
        if ip not in used_ips:
            used_ips.add(ip)
            return ip
    return None

def smallest_supernet(start_ip, end_ip):
    # Returns the smallest IPv4Network that contains both start_ip and end_ip
    start = int(ipaddress.IPv4Address(start_ip))
    end = int(ipaddress.IPv4Address(end_ip))
    # Find the smallest prefix length that covers both
    for prefix in range(32, 0, -1):
        net = ipaddress.IPv4Network(f"{start_ip}/{prefix}", strict=False)
        if int(ipaddress.IPv4Address(net.network_address)) <= start and int(ipaddress.IPv4Address(net.broadcast_address)) >= end:
            return net
    raise ValueError(f"Could not find supernet for {start_ip} - {end_ip}")

def build_butane_config(merged, butane_version='1.5.0', iprange=None, used_ips=None, server_key=None):
    butane = {
        'variant': 'fcos',
        'version': butane_version,
    }
    # Users (KISS: user, password, ssh_keys, groups)
    user_name = merged.get('user', 'core')
    user_block = {'name': user_name}
    if 'password' in merged and 'password_hash' not in merged:
        user_block['password_hash'] = crypt(merged['password'], mksalt(METHOD_SHA512))
    elif 'password_hash' in merged:
        user_block['password_hash'] = merged['password_hash']
    if 'groups' in merged:
        user_block['groups'] = merged['groups']
    if 'ssh_keys' in merged:
        user_block['ssh_authorized_keys'] = merged['ssh_keys']
    butane['passwd'] = {'users': [user_block]}
    # Storage: motd, hostname, extra files/dirs (KISS)
    storage = {}
    files = []
    directories = []
    # motd
    if 'motd' in merged:
        files.append({
            'path': '/etc/motd',
            'mode': 0o644,
            'overwrite': True,
            'contents': {'inline': merged['motd']}
        })
    # hostname
    if 'hostname' in merged:
        files.append({
            'path': '/etc/hostname',
            'mode': 0o644,
            'overwrite': True,
            'contents': {'inline': merged['hostname']}
        })
    # extra files/dirs
    for f in merged.get('files', []):
        mode = f.get('mode', 0o644)
        # Use 0600 for nmconnection files
        if f['path'].startswith('/etc/NetworkManager/system-connections/'):
            mode = 0o600
        files.append({
            'path': f['path'],
            'mode': mode,
            'overwrite': f.get('overwrite', True),
            'contents': {'inline': f['contents']}
        })
    for d in merged.get('directories', []):
        directories.append({
            'path': d['path'],
            'mode': d.get('mode', 0o755)
        })
    # Network (KISS: dhcp or static, server always overrides global)
    network = merged.get('network', {})
    # Assign IP from iprange if needed
    if iprange and len(iprange) > 0 and used_ips is not None and network.get('dhcp') is False:
        if not network.get('address'):
            ip = assign_ip_from_range(iprange, used_ips)
            if ip:
                # Use mask from config if provided, else calculate from iprange
                mask = network.get('mask')
                if not mask:
                    # Always infer netmask from the full iprange, not from the assigned IP
                    # iprange is a list of IPs, so use the first and last for the range
                    start_ip = iprange[0]
                    end_ip = iprange[-1]
                    net = smallest_supernet(start_ip, end_ip)
                    mask = str(net.prefixlen)
                network['address'] = f"{ip}/{mask}"
    if network:
        iface = network.get('interface', 'eth0')
        if network.get('dhcp', True) is False:
            dns_list = network.get('dns', [])
            if not isinstance(dns_list, list) or not all(isinstance(dns, str) for dns in dns_list):
                dns_list = []
            nm_contents = (
                f"[connection]\nid={iface}\ntype=ethernet\ninterface-name={iface}\n"
                f"[ipv4]\naddress1={network.get('address','')},{network.get('gateway','')}\n"
                f"dns={' '.join(dns_list)}\ndns-search=\nmay-fail=false\nmethod=manual"
            )
            files.append({
                'path': f'/etc/NetworkManager/system-connections/{iface}.nmconnection',
                'mode': 0o600,
                'overwrite': network.get('overwrite', True),
                'contents': {'inline': nm_contents}
            })
    # LUKS/clevis/tpm2/sss support (KISS)
    luks = merged.get('luks')
    if luks:
        luks_entry = {
            'name': luks.get('name', 'luks_root'),
            'device': luks['device'],
            'wipe_volume': luks.get('wipe_volume', True),
            'label': luks.get('label', 'luks_root'),
            'key_file': luks.get('key_file'),
        }
        clevis = luks.get('clevis')
        if clevis:
            clevis_entry = dict(clevis)
            luks_entry['clevis'] = clevis_entry
        butane.setdefault('storage', {})
        butane['storage'].setdefault('luks', []).append(luks_entry)
        # Filesystem mapping for root
        if luks.get('mount_root', True):
            butane['storage'].setdefault('filesystems', []).append({
                'device': f"/dev/mapper/{luks_entry['name']}",
                'format': 'xfs',
                'path': '/',
                'wipe_filesystem': luks.get('wipe_filesystem', True)
            })
    # LUKS boot device support (KISS, direct mapping, filter out 'device', support clevis/sss)
    boot_device = merged.get('boot_device')
    if boot_device and 'luks' in boot_device:
        luks_cfg = dict(boot_device['luks'])
        luks_cfg.pop('device', None)  # Remove 'device' if present
        # If a clevis block is present, copy it as-is (including sss, tang, tpm2, threshold, etc)
        if 'clevis' in luks_cfg:
            # Ensure clevis is a dict and copy as-is
            luks_cfg['clevis'] = dict(luks_cfg['clevis'])
        butane['boot_device'] = {'luks': luks_cfg}
    # Deduplicate files by path (server wins)
    files = dedup_files(files)
    if files:
        butane.setdefault('storage', {})
        butane['storage']['files'] = files
    if directories:
        butane.setdefault('storage', {})
        butane['storage']['directories'] = directories
    # Services (KISS: simple list)
    svc_map = {'docker': 'docker.socket', 'podman': 'podman.socket', 'cockpit': 'cockpit.socket', 'tailscale': 'tailscaled.service', 'nfs': 'nfs-server.service', 'samba': 'smb.service', 'libvirtd': 'libvirtd.socket'}
    units = []
    for svc in merged.get('services', []):
        if svc in svc_map:
            units.append({'name': svc_map[svc], 'enabled': True})
        else:
            units.append({'name': svc, 'enabled': True})
    for u in merged.get('systemd_units', []):
        units.append(u)
    # Always add ucore autorebase directory
    directories.append({
        'path': '/etc/ucore-autorebase',
        'mode': 0o754  # 0754 as in ucore example
    })

    # Always add ucore autorebase systemd units (using image from config)
    image = merged.get('image', 'ghcr.io/ublue-os/ucore-hci:stable')
    unsigned_unit = {
        'name': 'ucore-unsigned-autorebase.service',
        'enabled': True,
        'contents': f'''[Unit]
Description=uCore autorebase to unsigned OCI and reboot
ConditionPathExists=!/etc/ucore-autorebase/unverified
ConditionPathExists=!/etc/ucore-autorebase/signed
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
StandardOutput=journal+console
ExecStart=/usr/bin/rpm-ostree rebase --bypass-driver ostree-unverified-registry:{image}
ExecStart=/usr/bin/touch /etc/ucore-autorebase/unverified
ExecStart=/usr/bin/systemctl disable ucore-unsigned-autorebase.service
ExecStart=/usr/bin/systemctl reboot

[Install]
WantedBy=multi-user.target
'''
    }
    signed_unit = {
        'name': 'ucore-signed-autorebase.service',
        'enabled': True,
        'contents': f'''[Unit]
Description=uCore autorebase to signed OCI and reboot
ConditionPathExists=/etc/ucore-autorebase/unverified
ConditionPathExists=!/etc/ucore-autorebase/signed
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
StandardOutput=journal+console
ExecStart=/usr/bin/rpm-ostree rebase --bypass-driver ostree-image-signed:docker://{image}
ExecStart=/usr/bin/touch /etc/ucore-autorebase/signed
ExecStart=/usr/bin/systemctl disable ucore-signed-autorebase.service
ExecStart=/usr/bin/systemctl reboot

[Install]
WantedBy=multi-user.target
'''
    }
    units.extend([unsigned_unit, signed_unit])
    if units:
        butane['systemd'] = {'units': units}
    if 'kernel_arguments' in merged:
        ka = merged['kernel_arguments']
        if isinstance(ka, list):
            butane['kernel_arguments'] = {'should_exist': ka}
        elif isinstance(ka, dict):
            butane['kernel_arguments'] = ka
    if 'timezone' in merged:
        butane['timezone'] = merged['timezone']
    if 'ntp' in merged:
        butane['ntp'] = merged['ntp']
    return butane

def validate_input_yaml(cfg):
    # Basic checks for common mistakes
    if 'servers' not in cfg:
        raise ValueError("Missing 'servers' key at top level.")
    if not isinstance(cfg['servers'], dict):
        raise ValueError("'servers' must be a dictionary.")
    # Warn about common mistakes
    for sname, server in cfg['servers'].items():
        if 'files' in server:
            for f in server['files']:
                if isinstance(f.get('contents'), dict):
                    continue
                if not isinstance(f.get('contents'), str):
                    print(f"Warning: In server '{sname}', file '{f.get('path')}' contents should be a string. Auto-fixing.")
        if 'kernel_arguments' in server and not isinstance(server['kernel_arguments'], (list, dict)):
            print(f"Warning: In server '{sname}', kernel_arguments should be a list or object. Auto-fixing.")

def main():
    parser = argparse.ArgumentParser(description="Merge config and generate Butane YAML for a server (KISS mode)")
    parser.add_argument('--config', required=True, help='Input config.yaml')
    parser.add_argument('--server', required=True, help='Server key')
    parser.add_argument('--output', required=True, help='Output Butane YAML file')
    parser.add_argument('--butane-version', default='1.6.0', help='Butane config version (default: 1.6.0)')
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    try:
        validate_input_yaml(cfg)
    except Exception as e:
        print(f"YAML validation error: {e}", file=sys.stderr)
        sys.exit(1)
    global_cfg = cfg.get('global', {})
    server_cfg = cfg['servers'][args.server]
    merged = merge_configs(global_cfg, server_cfg)
    # Handle global iprange for static IP assignment
    iprange = None
    used_ips = set()
    if 'network' in global_cfg and 'iprange' in global_cfg['network']:
        iprange = get_ip_range(global_cfg['network']['iprange'])
    # Collect already assigned IPs
    for sname, scfg in cfg['servers'].items():
        n = scfg.get('network', {})
        if n.get('address'):
            used_ips.add(n['address'].split('/')[0])
    butane = build_butane_config(merged, butane_version=args.butane_version, iprange=iprange, used_ips=used_ips, server_key=args.server)
    with open(args.output, 'w') as f:
        yaml.dump(butane, f, default_flow_style=False, sort_keys=False)

if __name__ == '__main__':
    main()
