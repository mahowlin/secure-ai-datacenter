"""Experimental and off the GitOps render path. The orchestrator does not call this module.

Render a finite, read-only node runtime preflight for the held route package.

The Pod mounts host /usr read-only and performs installer rotation only in a
private memory emptyDir. It does not receive an API token or mount CNI paths.
Delete all four objects by exact name after collecting status and logs.
"""

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from resolver import resolve

SOURCE = ROOT / 'experiments/hcp-worker-guest-route'
NAME = 'helper2-peer-route-runtime-preflight'
NAMESPACE = 'openshift-gitops'

COMMAND = r'''set -euo pipefail
umask 077
for tool in bash python3 flock sha256sum cmp mktemp; do command -v "$tool" >/dev/null; done
python3 -c 'import base64, hashlib, ipaddress, json, ssl, subprocess, urllib.request; print("container-python-ssl-ok")'
test -x /host-usr/bin/python3
PYTHONHOME=/host-usr /host-usr/bin/python3 -c 'import hashlib, ipaddress, json, ssl, subprocess, urllib.request; print("host-python-stdlib-ssl-ok")'
test -x /host-usr/bin/nsenter
/host-usr/bin/nsenter --version >/dev/null
if test -x /host-usr/sbin/ip; then /host-usr/sbin/ip -Version >/dev/null; else /host-usr/bin/ip -Version >/dev/null; fi
printf 'host-command-dependencies-ok\n'
mkdir -p /scratch/bin /scratch/conf /scratch/cred
test -L /source/peer-route-mirror.py
cp /source/peer-route-mirror.py /scratch/helper.py
printf 'public-test-ca\n' > /scratch/cred/ca.crt
python3 -c 'import base64,json,time,sys; p=base64.urlsafe_b64encode(json.dumps({"exp":int(time.time())+int(sys.argv[1])}).encode()).decode().rstrip("="); open("/scratch/cred/token","w").write("header."+p+".signature")' 600
export HCP_ROUTE_CLUSTER=example-tenant-a HCP_ROUTE_POD_UID=12345678123412341234123456789abc
export HCP_ROUTE_HELPER_SHA256="$(sha256sum /scratch/helper.py | cut -d ' ' -f 1)"
export HCP_ROUTE_HELPER_SOURCE=/scratch/helper.py HCP_ROUTE_BIN_DIR=/scratch/bin
export HCP_ROUTE_CONF_DIR=/scratch/conf HCP_ROUTE_TOKEN_SOURCE=/scratch/cred/token
export HCP_ROUTE_CA_SOURCE=/scratch/cred/ca.crt HCP_ROUTE_HOST_PYTHON=/host-usr/bin/python3
bash /source/peer-route-node-sync.sh install
bash /source/peer-route-node-sync.sh verify
python3 -c 'import base64,json,time,sys; p=base64.urlsafe_b64encode(json.dumps({"exp":int(time.time())+int(sys.argv[1])}).encode()).decode().rstrip("="); open("/scratch/cred/token","w").write("header."+p+".signature")' 500
if bash /source/peer-route-node-sync.sh verify >/dev/null 2>&1; then exit 2; fi
bash /source/peer-route-node-sync.sh refresh
bash /source/peer-route-node-sync.sh verify
HCP_ROUTE_ALLOW_REMOVE=reviewed bash /source/peer-route-node-sync.sh remove
test ! -e "/scratch/bin/saif-example-tenant-a-route-${HCP_ROUTE_HELPER_SHA256:0:12}"
printf 'private-scratch-installer-rotation-ok\n'
test -L /source/helper1-5430-route-mirror
cp /source/helper1-5430-route-mirror /scratch/legacy-helper
export HCP_ROUTE_LEGACY_SOURCE=/scratch/legacy-helper
export HCP_ROUTE_LEGACY_SHA256="$(sha256sum /scratch/legacy-helper | cut -d ' ' -f 1)"
bash /source/peer-route-retained-sync.sh install
bash /source/peer-route-retained-sync.sh verify
HCP_ROUTE_ALLOW_REMOVE=reviewed bash /source/peer-route-retained-sync.sh remove
test ! -e /scratch/bin/helper1-5430-route-mirror
printf 'projected-source-legacy-lifecycle-ok\n'
'''


def objects():
    platform = resolve(ROOT)
    node = platform.defaults.identity['preflightNode']
    image = platform.defaults.gitops_worker_ipam['image']
    if '@sha256:' not in image:
        raise ValueError('preflight image must be digest pinned')
    scc = {'apiVersion': 'security.openshift.io/v1', 'kind': 'SecurityContextConstraints',
           'metadata': {'name': NAME}, 'priority': 0,
           'allowPrivilegedContainer': False, 'allowPrivilegeEscalation': False,
           'allowHostNetwork': False, 'allowHostPID': False, 'allowHostIPC': False,
           'allowHostPorts': False, 'allowHostDirVolumePlugin': True,
           'readOnlyRootFilesystem': True, 'allowedCapabilities': [],
           'defaultAddCapabilities': [], 'requiredDropCapabilities': ['ALL'],
           'runAsUser': {'type': 'MustRunAs', 'uid': 0},
           'seLinuxContext': {'type': 'RunAsAny'}, 'fsGroup': {'type': 'RunAsAny'},
           'supplementalGroups': {'type': 'RunAsAny'}, 'seccompProfiles': ['runtime/default'],
           'volumes': ['configMap', 'emptyDir', 'hostPath'],
           'users': [f'system:serviceaccount:{NAMESPACE}:{NAME}']}
    sa = {'apiVersion': 'v1', 'kind': 'ServiceAccount',
          'metadata': {'name': NAME, 'namespace': NAMESPACE},
          'automountServiceAccountToken': False}
    cm = {'apiVersion': 'v1', 'kind': 'ConfigMap',
          'metadata': {'name': NAME, 'namespace': NAMESPACE},
          'data': {'peer-route-mirror.py': (SOURCE / 'peer-route-mirror.py').read_text(),
                   'peer-route-node-sync.sh': (SOURCE / 'peer-route-node-sync.sh').read_text(),
                   'helper1-5430-route-mirror': (SOURCE / 'helper1-5430-route-mirror').read_text(),
                   'peer-route-retained-sync.sh': (SOURCE / 'peer-route-retained-sync.sh').read_text()}}
    pod = {'apiVersion': 'v1', 'kind': 'Pod',
           'metadata': {'name': NAME, 'namespace': NAMESPACE,
                        'labels': {'app': NAME}},
           'spec': {'serviceAccountName': NAME, 'automountServiceAccountToken': False,
                    'restartPolicy': 'Never', 'nodeSelector': {'kubernetes.io/hostname': node},
                    'activeDeadlineSeconds': 600,
                    'containers': [{'name': 'preflight', 'image': image,
                                    'imagePullPolicy': 'IfNotPresent',
                                    'command': ['/bin/bash', '-ec'], 'args': [COMMAND],
                                    'securityContext': {'runAsUser': 0,
                                                        'allowPrivilegeEscalation': False,
                                                        'readOnlyRootFilesystem': True,
                                                        'capabilities': {'drop': ['ALL']},
                                                        'seccompProfile': {'type': 'RuntimeDefault'}},
                                    'volumeMounts': [
                                        {'name': 'source', 'mountPath': '/source', 'readOnly': True},
                                        {'name': 'scratch', 'mountPath': '/scratch'},
                                        {'name': 'host-usr', 'mountPath': '/host-usr', 'readOnly': True}],
                                    'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'},
                                                  'limits': {'cpu': '500m', 'memory': '256Mi'}}}],
                    'volumes': [
                        {'name': 'source', 'configMap': {'name': NAME}},
                        {'name': 'scratch', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '4Mi'}},
                        {'name': 'host-usr', 'hostPath': {'path': '/usr', 'type': 'Directory'}}]}}
    return [scc, sa, cm, pod]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.write_text(yaml.safe_dump_all(objects(), sort_keys=False))
