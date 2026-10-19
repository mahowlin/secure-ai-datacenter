"""Budget changes must drive only the selected management collector."""
import copy
import json
import os
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from gitops_renderer.applications import _render_cluster_entrypoints, _append_management_exporter_parameters
from gitops_renderer.selected_inputs import selected
from management_exporter import management_exporter_parameters
from resolver import resolve, validate
PARAMETER = 'customConfig.sources.connection_logs.max_line_bytes'
class ManagementVectorLineBoundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.platform = resolve(ROOT)
        cls.cluster = next(c for c in cls.platform.clusters if 'managementExporter' in c.observability)
    def parameters(self, cluster):
        with selected(self.platform.defaults):
            files = _render_cluster_entrypoints(cluster, 'https://example.test/gitops', 'review', self.platform.defaults)
        root = yaml.safe_load(files[f'clusters/{cluster.name}/kustomization.yaml'])
        patch = next(p for p in root['patches'] if p['target'].get('name') == 'vector-exporter')
        return [p['value'] for p in yaml.safe_load(patch['patch'])]
    def test_changed_inputs_control_selected_budget(self):
        original = self.parameters(self.cluster)
        for mib in (8, 24):
            c = copy.deepcopy(self.cluster); v = c.observability['managementExporter']
            v['expectedProducerWriterMaxSizeMiB'] = mib
            v['connectionLogMaxLineBytes'] = v['profile']['maxLineBytes'] = mib*1048576
            v['requestConcurrency'] = v['profile']['requestConcurrency'] = 2
            result = self.parameters(c)
            self.assertEqual([p['value'] for p in result if p['name'] == PARAMETER], [str(mib*1048576)])
            self.assertEqual([p['value'] for p in result if p['name'].endswith('request.concurrency')], ['2'])
            self.assertEqual([p for p in result if p['name'] not in {PARAMETER,'customConfig.sinks.timescape_push_api.request.concurrency'}], [p for p in original if p['name'] not in {PARAMETER,'customConfig.sinks.timescape_push_api.request.concurrency'}])
    def test_absent_override_preserves_existing_consumer(self):
        c = copy.deepcopy(self.cluster); del c.observability['managementExporter']
        self.assertNotIn(PARAMETER, [p['name'] for p in self.parameters(c)])
    def test_mismatched_bounds_capacity_and_threads_fail_closed(self):
        good = self.cluster.observability['managementExporter']
        variants = [None, {}, [], {**good, 'connectionLogMaxLineBytes': True},
                    {**good, 'connectionLogMaxLineBytes': 1}, {**good, 'memoryLimitMiB': 512},
                    {**good, 'memoryRequestMiB': 1}, {**good, 'cpuLimitMillicores': 500},
                    {**good, 'args': ['--threads','2']}, {**good, 'requestConcurrency': 2},
                    {**good, 'misspelled': 1}]
        for value in variants:
            with self.subTest(value=value):
                p = copy.deepcopy(self.platform); c = next(c for c in p.clusters if c.name == self.cluster.name)
                c.observability['managementExporter'] = value
                self.assertTrue(any('managementExporter' in e for e in validate(p)))
                with self.assertRaisesRegex(ValueError,'managementExporter'): self.parameters(c)
    def test_owner_selected_semantically_and_ambiguity_rejected(self):
        unrelated = {'target': {'kind':'Application','name':'unrelated'}, 'patch':'[]'}
        owner = {'target': {'kind':'Application','name':'vector-exporter'}, 'patch':'[]'}
        patches = [unrelated, copy.deepcopy(unrelated), owner]
        _append_management_exporter_parameters(patches,[{'name':'test','value':'3'}])
        self.assertEqual(unrelated['patch'],'[]')
        self.assertIn('name: test',owner['patch'])
        for patches in ([unrelated], [owner, copy.deepcopy(owner)]):
            with self.assertRaisesRegex(ValueError,'exactly one'): _append_management_exporter_parameters(patches,[])

    def test_profile_matches_selected_candidate_and_effective_controls(self):
        c = json.loads((ROOT/'tests/fixtures/management-vector-profile.json').read_text())
        selected = yaml.safe_load((ROOT/c['candidate_source']).read_text())['spec']['day2GitOps']['observability']['managementExporter']
        self.assertEqual(selected, self.cluster.observability['managementExporter'])
        self.assertEqual(c['vector_memory_bytes'], selected['memoryLimitMiB']*1048576)
        self.assertEqual(c['vector_cpus']*1000, selected['cpuLimitMillicores'])
        self.assertEqual(c['threads'], int(selected['args'][selected['args'].index('--threads')+1]))
        self.assertEqual(c['reader_max_line_bytes'], selected['connectionLogMaxLineBytes'])
        config = json.loads((ROOT/c['config']).read_text())
        self.assertEqual(config['sinks']['timescape_push_api']['request']['concurrency'], selected['requestConcurrency'])
        self.assertEqual(config['sources']['connection_logs']['max_line_bytes'], c['reader_max_line_bytes'])
        self.assertEqual(config['sinks']['timescape_push_api']['buffer']['max_size'], c['disk_buffer_bytes'])
        self.assertEqual(config['sinks']['timescape_push_api']['batch']['max_bytes'], c['http_batch_bytes'])
        self.assertEqual(c['image'], 'registry.example.com:5000/isovalent/vector@sha256:d621ac6a93242dc22b74289887da06ede13b543acfcdc8475e425da5bb8676ab')

    def test_workflow_checks_earlier_qa_change_after_docs_commit(self):
        code = """
import os, subprocess
paths = subprocess.check_output(["git", "diff", "--name-only", os.environ["PR_ROOT_SHA"] + "..." + os.environ["PR_HEAD_SHA"]], text=True).splitlines()
needed = any(p.startswith(("scripts/qa/", "tests/fixtures/management-vector-", "data/clusters/example-management/cluster.yaml", "scripts/management_exporter.py", "tests/test_management_vector_line_bound.py")) for p in paths)
with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
    handle.write("profile=" + str(needed).lower() + "\\n")
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args): return subprocess.check_output(['git', *args], cwd=root, text=True).strip()
            git('init','-q'); git('config','user.email','qa@example.test'); git('config','user.name','QA')
            (root/'README').write_text('base'); git('add','.'); git('commit','-qm','base'); base=git('rev-parse','HEAD')
            (root/'scripts/qa').mkdir(parents=True); (root/'scripts/qa/profile.py').write_text('change')
            git('add','.'); git('commit','-qm','QA change')
            (root/'README').write_text('docs only'); git('add','.'); git('commit','-qm','docs')
            output=root/'output'; env={**os.environ,'PR_ROOT_SHA':base,'PR_HEAD_SHA':git('rev-parse','HEAD'),'GITHUB_OUTPUT':str(output)}
            subprocess.run([sys.executable,'-c',code],cwd=root,env=env,check=True)
            self.assertEqual(output.read_text(),'profile=true\n')
            output.unlink(); env['PR_ROOT_SHA']=env['PR_HEAD_SHA']
            subprocess.run([sys.executable,'-c',code],cwd=root,env=env,check=True)
            self.assertEqual(output.read_text(),'profile=false\n')
