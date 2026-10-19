#!/usr/bin/env python3
"""Resolve optional boot-model placeholders from observed per-server disk models.

Use a hostname/serial mapping from the selected cluster definition or explicit
--serials hostname=serial pairs. No RAID/model inference from physical disk count.
Ambiguous hardware requires reviewed declared rootDeviceHints instead.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

import yaml

PLACEHOLDER = 'BOOT_DISK_MODEL_PLACEHOLDER'


def query(isctl, kind, serial):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', serial):
        raise ValueError('invalid declared server serial')
    result = subprocess.run([isctl, 'get', 'storage', kind, '--filter',
                             f"contains(Dn,'{serial}/Storage/MSTOR-RAID')", '-o', 'json'],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError('runtime disk observation failed')
    rows = json.loads(result.stdout)
    if not isinstance(rows, list):
        raise ValueError('disk observation must be an array')
    # Check actual subject identity as well as the remote query filter.
    if any(not isinstance(row, dict) or f'{serial}/Storage/MSTOR-RAID' not in row.get('Dn', '') for row in rows):
        raise ValueError('disk observation does not match the declared server/controller')
    return rows


def determine_boot_model(isctl, serial):
    virtual = query(isctl, 'virtualdrive', serial)
    if len(virtual) > 1:
        raise ValueError('multiple virtual drives require an explicit reviewed boot identity')
    if virtual:
        model = virtual[0].get('Model')
    else:
        disks = query(isctl, 'physicaldisk', serial)
        if len(disks) != 1:
            raise ValueError('no unique observed passthrough disk; cannot infer RAID from disk count')
        model = disks[0].get('Model')
    if not isinstance(model, str) or not model.strip():
        raise ValueError('observed boot device has no model')
    return model.strip().replace(' ', '_')


def node_serials(document):
    nodes = document.get('spec', {}).get('agentConfig', {}).get('nodes', [])
    if not nodes:
        nodes = document.get('cluster', {}).get('nodes', [])
    pairs = {}
    for node in nodes:
        name = node.get('hostname') or node.get('name')
        if not name or not node.get('serial') or name in pairs:
            raise ValueError('each declared node needs one unique hostname and serial')
        pairs[name] = node['serial']
    return pairs


def enrich(document, pairs, isctl):
    hosts = document.get('hosts', [])
    seen = set()
    for host in hosts:
        if host.get('rootDeviceHints', {}).get('model') != PLACEHOLDER:
            continue
        name = host.get('hostname', '')
        matches = [serial for hostname, serial in pairs.items()
                   if hostname == name or hostname == name.split('.')[0]]
        if len(matches) != 1 or matches[0] in seen:
            raise ValueError('placeholder host needs one unique hostname/serial mapping')
        seen.add(matches[0])
        host['rootDeviceHints']['model'] = determine_boot_model(isctl, matches[0])
    if PLACEHOLDER in yaml.safe_dump(document):
        raise ValueError('unresolved boot-model placeholder')
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('agent_config', type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--cluster-yaml', type=Path)
    group.add_argument('--serials', help='hostname=serial pairs separated by commas; never positional')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    document = yaml.safe_load(args.agent_config.read_text())
    if PLACEHOLDER not in yaml.safe_dump(document):
        print('No boot-model placeholders; no runtime lookup performed.')
        return
    if args.cluster_yaml:
        pairs = node_serials(yaml.safe_load(args.cluster_yaml.read_text()))
    else:
        pairs = {}
        for pair in args.serials.split(','):
            name, serial = pair.strip().split('=', 1)
            if not name or not serial or name in pairs:
                raise ValueError('invalid or duplicate hostname=serial pair')
            pairs[name] = serial
    isctl = shutil.which('isctl')
    if not isctl:
        raise ValueError('isctl is required for optional runtime enrichment')
    result = yaml.safe_dump(enrich(document, pairs, isctl), sort_keys=False)
    if args.dry_run:
        print(result)
    else:
        args.agent_config.write_text(result)


if __name__ == '__main__':
    main()
