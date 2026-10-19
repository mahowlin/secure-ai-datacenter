"""One-off synthetic offline qualification; no production API or credentials."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid


def docker(*args, timeout=90):
    r = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f'docker {args[0]} failed: {r.stderr[-2000:]}')
    return r.stdout.strip()


def inspect(name):
    return json.loads(docker('inspect', name))[0]


def memory(pid, shared=False):
    # Fail closed if the CI process namespace cannot see the actual Vector PID.
    p = Path('/proc') / str(pid)
    if p.joinpath('comm').read_text().strip() != 'vector':
        raise RuntimeError('Actual Vector process peak is inaccessible in this CI namespace')
    out = {}
    for line in p.joinpath('status').read_text().splitlines():
        if line.startswith(('VmRSS:', 'VmHWM:')):
            k, v, _ = line.split(); out[k.rstrip(':')] = int(v) * 1024
    if 'VmHWM' not in out:
        raise RuntimeError('Vector native process memory high-water mark unavailable')
    try:
        if shared: raise OSError('Sibling mount namespace is not the Vector cgroup')
        cg = next(x.split(':', 2)[2] for x in p.joinpath('cgroup').read_text().splitlines() if x.startswith('0::'))
        root = Path('/sys/fs/cgroup') / cg.lstrip('/')
        out['cgroup_peak'] = int(root.joinpath('memory.peak').read_text())
        out['cgroup_events'] = root.joinpath('memory.events').read_text()
        out['cgroup_stat'] = root.joinpath('memory.stat').read_text()
    except (OSError, StopIteration):
        out['cgroup_peak'] = None
    return out


# Executed only inside the isolated standard-library sink child.
def sink_main(inputs):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    cfg = json.loads(Path(inputs).read_text())
    root = Path('/fixtures'); root.mkdir(exist_ok=True)
    expected = {}; accepted = {}; offered = {}; requests = [0]; failures = [0]
    lock = threading.Lock()
    def write_status():
        tmp = root / 'status.tmp'
        tmp.write_text(json.dumps({'expected': expected, 'accepted': accepted, 'offered': offered,
                                  'requests': requests[0], 'failures': failures[0]}))
        tmp.replace(root / 'status.json')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            length = int(self.headers.get('Content-Length', '-1'))
            if self.path != '/push' or not 0 <= length <= 64 * 1024 * 1024:
                self.send_error(400); return
            body = self.rfile.read(length)
            digests = [hashlib.sha256(line).hexdigest() for line in body.splitlines() if line]
            with lock:
                requests[0] += 1
                for h in digests: offered[h] = offered.get(h, 0) + 1
                retry = failures[0] < cfg['fail_requests'] and any(len(line) >= max(cfg['line_bytes']) for line in body.splitlines())
                if retry: failures[0] += 1
            time.sleep(cfg['slow_seconds'] if retry else 0)
            if not retry:
                with lock:
                    for h in digests: accepted[h] = accepted.get(h, 0) + 1
            with lock: write_status()
            self.send_response(503 if retry else 200); self.end_headers(); self.wfile.write(b'retry' if retry else b'ok')
    def canonical(x): return json.dumps(x, separators=(',', ':'), sort_keys=True).encode()
    def emit(path, obj, connection=False):
        raw = canonical(obj)
        final = obj if connection else json.loads(raw)
        if 'flow' in final:
            final['flow']['source']['cluster_name'] = cfg['cluster_name']
            final['flow']['destination']['cluster_name'] = cfg['cluster_name']
        h = hashlib.sha256(canonical(final)).hexdigest()
        with lock: expected[h] = expected.get(h, 0) + 1
        with path.open('ab') as f: f.write(raw + b'\n')
    for f in ['connectionlog.log', 'hubble.log', 'k8s-events.log', 'application-model.log']:
        root.joinpath(f).touch()
    write_status()
    def producer():
        while not root.joinpath('start').exists(): time.sleep(.2)
        # Initial co-load window measures baseline before connection batches.
        start = time.monotonic(); sequence = 0
        end = start + (len(cfg['line_bytes']) + 1) * cfg['cadence_seconds']
        due = start + cfg['cadence_seconds']; batch = 0
        entry = {
            'source': {'kubernetes': {'cluster_name': cfg['cluster_name'], 'namespace': 'synthetic', 'pod_name': 'source', 'node_name': 'offline-node', 'ip': '192.0.2.1', 'port': 12345, 'ip_protocol': 6, 'resource_kind': 'RESOURCE_KIND_WORKLOAD', 'resource_name': 'source', 'workload_kind': 'WORKLOAD_KIND_DEPLOYMENT'}},
            'destination': {'kubernetes': {'cluster_name': cfg['cluster_name'], 'namespace': 'synthetic', 'pod_name': 'destination', 'ip': '198.51.100.1', 'port': 8080, 'ip_protocol': 6, 'resource_kind': 'RESOURCE_KIND_WORKLOAD', 'resource_name': 'destination', 'workload_kind': 'WORKLOAD_KIND_DEPLOYMENT'}},
            'observation_point': 'OBSERVATION_POINT_SOURCE',
            'links': [{'routing_telemetry': {'routing_forwarded_total': 12, 'routing_dropped_total': 0, 'routing_error_total': 0, 'routing_audit_total': 0, 'routing_redirected_total': 0}}]}
        while time.monotonic() < end:
            now = time.monotonic()
            if batch < len(cfg['line_bytes']) and now >= due:
                size = cfg['line_bytes'][batch]
                obj = {'uuid': str(uuid.UUID(int=batch + 1)), 'emitter': {'name': 'synthetic', 'version': 'offline', 'observer': {'name': 'synthetic', 'identifier': 'offline-node'}},
                       'window_start': '2026-01-01T00:00:00Z', 'window_end': '2026-01-01T00:00:10Z', 'connections': []}
                n = max(1, (size - len(canonical(obj))) // (len(canonical(entry)) + 1) - 1)
                obj['connections'] = [entry] * n
                pad = size - len(canonical(obj))
                if pad < 0 or pad > 2048: raise RuntimeError('Fixture shape/size mismatch')
                obj['emitter']['observer']['identifier'] += 'x' * pad
                if len(canonical(obj)) != size: raise RuntimeError('Fixture size not exact')
                emit(root/'connectionlog.log', obj, True)
                batch += 1; due += cfg['cadence_seconds']
                del obj
            for _ in range(cfg['flow_records_per_second'] // 10):
                sequence += 1
                emit(root/'hubble.log', {'flow': {'uuid': str(uuid.UUID(int=sequence)), 'source': {'namespace': 'synthetic', 'pod_name': 'source', 'labels': ['k8s:synthetic-label-'+str(i)+'=offline-workload-value' for i in range(cfg['flow_metadata_labels'])]}, 'destination': {'namespace': 'synthetic', 'pod_name': 'destination', 'labels': ['k8s:app=offline-destination']}, 'IP': {'source': '192.0.2.1', 'destination': '198.51.100.1'}, 'l4': {'TCP': {'source_port': 12345, 'destination_port': 8080}}, 'verdict': 'FORWARDED', 'time': '2026-01-01T00:00:00Z'}})
            time.sleep(.1)
        with lock: write_status()
        root.joinpath('done').write_text('done')
    threading.Thread(target=producer, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--inputs', required=True); ap.add_argument('--output'); ap.add_argument('--sink', action='store_true'); a = ap.parse_args()
    if a.sink: sink_main(a.inputs); return
    c = json.loads(Path(a.inputs).read_text()); result = {'synthetic': True, 'inputs': c, 'samples': [], 'success': False}
    result['candidate_source_sha256'] = hashlib.sha256(Path(c['candidate_source']).read_bytes()).hexdigest()
    config = json.loads(Path(c['config']).read_text())
    if (config['sources']['connection_logs']['max_line_bytes'] != c['reader_max_line_bytes']
            or config['sinks']['timescape_push_api']['request']['concurrency'] != 1
            or config['sinks']['timescape_push_api']['batch']['max_bytes'] != c['http_batch_bytes']
            or config['sinks']['timescape_push_api']['buffer']['max_size'] != c['disk_buffer_bytes']):
        raise RuntimeError('Declared fixture controls mismatch effective sandbox config')
    run = os.environ['GITHUB_RUN_ID']; prefix = 'helper2-vector-' + run; label = 'helper2.vector-profile=' + run
    network, fixtures, buffer = prefix+'-net', prefix+'-fixtures', prefix+'-buffer'; sink, vector = prefix+'-sink', prefix+'-vector'
    cleanup = []
    def interrupted(*_): raise RuntimeError('Profile interrupted; owned cleanup required')
    signal.signal(signal.SIGTERM, interrupted); signal.signal(signal.SIGINT, interrupted)
    try:
        if not shutil.which('docker'): raise RuntimeError('Existing Docker CLI unavailable; no installation allowed')
        info = json.loads(docker('info', '--format', '{{json .}}'))
        available = next(int(x.split()[1])*1024 for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))
        if info['NCPU'] < 2 or available < 3 * 1024**3: raise RuntimeError('Runner capacity below containment caps')
        result['runner'] = {'name': os.environ.get('RUNNER_NAME'), 'cpus': info['NCPU'], 'available_bytes': available}
        runtime = os.environ['QA_RUNTIME_IMAGE']
        meta = inspect(runtime)  # Must be an existing available runtime, no runtime installation.
        if meta['Architecture'] != 'amd64' or meta['Os'] != 'linux': raise RuntimeError('QA runtime architecture mismatch')
        result['qa_runtime_digests'] = meta.get('RepoDigests')
        docker('pull', '--platform', c['platform'], c['image'], timeout=120)
        meta = inspect(c['image'])
        if meta['Architecture'] != 'amd64' or meta['Os'] != 'linux' or c['image'] not in meta.get('RepoDigests', []): raise RuntimeError('Selected immutable Vector pin/architecture mismatch')
        result['vector_identity'] = {'id': meta['Id'], 'digests': meta['RepoDigests'], 'entrypoint': meta['Config']['Entrypoint']}
        docker('network', 'create', '--internal', '--label', label, network); cleanup.append(('network', network))
        for name, size in [(fixtures,c['fixture_volume_bytes']), (buffer,c['buffer_volume_bytes'])]:
            docker('volume', 'create', '--label', label, '--driver', 'local', '--opt', 'type=tmpfs', '--opt', 'device=tmpfs', '--opt', 'o=size='+str(size), name); cleanup.append(('volume',name))
        # Kernel tmpfs size limits hard-bound all mutable fixture/buffer disk. Charge
        # to cgroups is reported; process VmHWM is separately measured.
        common = ['--network',network,'--cap-drop','ALL','--security-opt','no-new-privileges','--read-only','--tmpfs','/tmp:rw,size=16777216', '--log-driver','json-file','--log-opt','max-size=4m','--log-opt','max-file=1','--label',label]
        qa = str(Path.cwd())
        docker('run','-d','--name',vector,*common,'--cpus',str(c['vector_cpus']),'--memory',str(c['vector_memory_bytes']),'--memory-swap',str(c['vector_memory_bytes']),'-e','CLUSTER_NAME='+c['cluster_name'],'-v',fixtures+':/fixtures:ro','-v',buffer+':/buffer','--mount','type=bind,src='+qa+'/'+c['config']+',dst=/qa/vector.json,readonly',c['image'],'--threads',str(c['threads']),'--config','/qa/vector.json'); cleanup.append(('container',vector))
        docker('run','-d','--name',sink,'--network-alias','sink','--pid','container:'+vector,*common,'--cpus',str(c['sink_cpus']),'--memory',str(c['sink_memory_bytes']),'--memory-swap',str(c['sink_memory_bytes']),'-v',fixtures+':/fixtures','-v',buffer+':/buffer:ro','--mount','type=bind,src='+qa+'/scripts/qa/management_vector_profile.py,dst=/qa/profile.py,readonly','--mount','type=bind,src='+qa+'/'+a.inputs+',dst=/qa/inputs.json,readonly','--entrypoint','python3',runtime,'/qa/profile.py','--sink','--inputs','/qa/inputs.json'); cleanup.append(('container',sink))
        time.sleep(3)
        def sandbox_memory():
            return json.loads(docker('exec',sink,'python3','-c', "import runpy,json; m=runpy.run_path('/qa/profile.py'); print(json.dumps(m['memory'](1, shared=True)))"))
        result['preflight_memory'] = sandbox_memory()
        result['measurement_method'] = 'Native Vector VmHWM from its private PID namespace; Python sibling has no host PID access. Cgroup charge sampled separately.'
        # No records are produced until identity/isolation/native peak access passes.
        result['vector_resources'] = {k: inspect(vector)['HostConfig'][k] for k in ('Memory','MemorySwap','NanoCpus')}
        if result['vector_resources'] != {'Memory': c['vector_memory_bytes'], 'MemorySwap': c['vector_memory_bytes'], 'NanoCpus': int(c['vector_cpus']*1000000000)}:
            raise RuntimeError('Actual Vector containment differs from selected candidate test controls')
        for name in [sink, vector]:
            x = inspect(name)
            if x['HostConfig']['Privileged'] or x['HostConfig']['NetworkMode'] != network or x['HostConfig'].get('PortBindings') or x['HostConfig'].get('PidMode') == 'host': raise RuntimeError('Isolation mismatch')
        docker('exec',sink,'python3','-c',"from pathlib import Path; Path('/fixtures/start').touch()")
        deadline = time.monotonic() + c['deadline_seconds']; done_since = None
        while time.monotonic() < deadline:
            state = inspect(vector)['State']
            if not state['Running']: result['vector_exit'] = state; raise RuntimeError('Vector exited during profile')
            m = sandbox_memory(); m['disk_used_bytes'] = int(docker('exec',sink,'python3','-c',"import os; s=os.statvfs('/buffer'); print((s.f_blocks-s.f_bfree)*s.f_frsize)")); m['elapsed_seconds'] = c['deadline_seconds'] - (deadline - time.monotonic()); m['container_memory_sample'] = docker('stats','--no-stream','--format','{{.MemUsage}}',vector); result['samples'].append(m)
            status = json.loads(docker('exec',sink,'python3','-c',"from pathlib import Path; print(Path('/fixtures/status.json').read_text())"))
            done = docker('exec',sink,'python3','-c',"from pathlib import Path; print(Path('/fixtures/done').exists())") == 'True'
            if done:
                done_since = done_since or time.monotonic()
                if status['expected'] and status['accepted'] == status['expected']:
                    result['delivery'] = status; result['success'] = True; result['memory_final'] = sandbox_memory(); break
                if time.monotonic() - done_since > c['drain_seconds']: result['delivery'] = status; raise RuntimeError('Complete count/hash drain failed')
            time.sleep(2)
        if not result['success']: raise RuntimeError('Profile deadline exceeded')
    except Exception as e:
        result['error'] = str(e)
    finally:
        errors = []
        for kind, name in reversed(cleanup):
            try:
                if kind == 'container':
                    if name == vector: result['vector_final_state'] = inspect(name)['State']
                    docker('rm','-f',name)
                else: docker(kind,'rm',name)
            except Exception as e: errors.append(str(e))
        result['cleanup_errors'] = errors
        result['cleanup_remaining'] = {}
        for kind,args in [('containers',('ps','-aq','--filter','label='+label)),('volumes',('volume','ls','-q','--filter','label='+label)),('networks',('network','ls','-q','--filter','label='+label))]:
            try: result['cleanup_remaining'][kind] = docker(*args)
            except Exception as e: result['cleanup_remaining'][kind] = 'unverified: '+str(e)
        Path(a.output).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({k:result.get(k) for k in ['success','error','cleanup_errors','cleanup_remaining']}))
    if not result['success'] or result['cleanup_errors'] or any(result['cleanup_remaining'].values()): sys.exit(1)


if __name__ == '__main__': main()
