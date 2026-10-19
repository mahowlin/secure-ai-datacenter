"""Prepared operator inputs are external, declared and reverified offline."""
from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest
import yaml
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from resolver import resolve

class OperatorArtifactInput(unittest.TestCase):
    def test_missing_or_changed_prepared_input_and_release_drift_fail(self):
        selected=next(p for p in (ROOT/'data/clusters').glob('*/local-registry.yaml')
                      if any((v or {}).get('publish') for k,v in yaml.safe_load(p.read_text())['spec'].items() if k in ('ocMirror','ocMirrorTrial')))
        policy=next(v for k,v in yaml.safe_load(selected.read_text())['spec'].items() if k in ('ocMirror','ocMirrorTrial'))
        install=policy['installation']
        self.assertFalse((ROOT/'vendor/oc-mirror-operator/v0.1.7').exists())
        self.assertTrue(install['csvPath'].startswith('.artifacts/'))
        for mode in ['missing','tamper','release-drift']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                clone=Path(tmp)/'selected'
                shutil.copytree(ROOT,clone,ignore=shutil.ignore_patterns('.git','generated','__pycache__','.venv'))
                csv=clone/install['csvPath']
                if mode=='missing':csv.unlink()
                elif mode=='tamper':csv.write_text(csv.read_text()+'\n# changed\n')
                else:
                    contract=clone/install['artifactContractPath'];value=json.loads(contract.read_text());value['commit']='0'*40;contract.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError,'operator artifact'):
                    resolve(clone)

if __name__=='__main__':unittest.main()
