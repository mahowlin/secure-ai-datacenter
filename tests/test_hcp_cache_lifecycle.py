"""Customer inventory changes and lifecycle failures; no registry or live writes."""
import copy,hashlib,json,sys,tempfile,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
import yaml
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from resolver import resolve
from hcp_cache import selection,load_contract,sha,reference,require_ready,production_resources,rewrite_images

class CacheLifecycle(unittest.TestCase):
 def setUp(self):
  self.platform=resolve(ROOT,load_cache=False);self.cluster=copy.deepcopy(next(c for c in self.platform.clusters if c.name=='example-management'));self.defaults=self.platform.defaults
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  # Real selected input files, in an isolated fixture root. No private BOM is used.
  import shutil
  shutil.copytree(ROOT/'data',self.root/'data');shutil.copy2(ROOT/'platform-release.yaml',self.root/'platform-release.yaml')
  c=self.cluster;r=c.local_registry;o=r['ocMirror'];r['cacheProduction']={'mode':'hosted-cache','inventory':{'path':'inventory.json','sha256':''},'siteRegistry':self.defaults.registry_url,'pullRegistry':'images.example-management.example.com:443','syncRegistry':f"{r['syncService']}.{r['namespace']}.svc:{r['port']}",'readerSecretRef':r['externalPull']['consumer']['pullSecret'],'writerSecretRef':'production-writer','siteWriterSecretRef':'site-producer-writer','producerTrust':{'mode':'system'},'runtimePrerequisites':{'readerSecretUID':'fixture-uid','readerSecretResourceVersion':'1','dnsEvidence':'fixture-dns','tlsEvidence':'fixture-tls'},'namespace':o['namespace'],'imageSet':'production-images','mirrorTarget':'production-cache','repositoryPrefix':'hosted-content','caConfigMap':'production-ca','releaseLookup':'preceding-registry','maxRoots':100,'maxAgeSeconds':300,'concurrency':1,'batchSize':1,'maxRetries':2,'pollInterval':'0s','checkExistInterval':'1h','resources':copy.deepcopy(o['resources'])}
  # Selection rendering is the actual source renderer; closure digests are synthetic fixture inputs.
  rp=self.root/'data/clusters/example-management/local-registry.yaml';doc=yaml.safe_load(rp.read_text());doc['spec']=copy.deepcopy(r);rp.write_text(yaml.safe_dump(doc,sort_keys=False))
  self.expected=selection(self.root,c,self.defaults)
  required=['release:'+h for h in self.expected['hostedReleases']]+['disk:'+h for h in self.expected['hostedReleases']]+['guest-cni:'+h for h in self.expected['guestCNI']]+['addon:'+a for a in self.expected['addons']]
  roots=[];sources={}
  from hcp_cache import repository
  for ref in self.expected['renderedImages']:
   repo=repository(ref);digest=ref.rsplit('@',1)[1] if '@' in ref else 'sha256:'+hashlib.sha256(ref.encode()).hexdigest();source=repo+'@'+digest
   if source in sources:sources[source]['requestedReferences'].append(ref);continue
   name=hashlib.sha256(repo.encode()).hexdigest()[:20];item={'source':source,'requestedReferences':[ref],'siteRepository':repo.split('/',1)[1],'cacheRepository':'hosted-content/'+name,'phase':'control-planes','scope':'preceding','architecture':self.expected['architecture'],'uses':['fixture-dependency']};sources[source]=item;roots.append(item)
  for h,ref in self.expected['hostedReleases'].items():next(r for r in roots if ref in r['requestedReferences'])['uses'].append('release:'+h)
  disk=next(r for r in roots if 'rhel-guest-image' in r['source']);disk['uses'] += [u for u in required if u.startswith('disk:')]
  vector={'source':'upstream.example/guest-component@sha256:'+hashlib.sha256(b'fixture-guest').hexdigest(),'requestedReferences':[],'siteRepository':'guest-component','cacheRepository':'hosted-content/guest-component','phase':'workers','scope':'cache','architecture':self.expected['architecture'],'uses':[u for u in required if u.startswith(('guest-cni:','addon:'))]};roots.append(vector)
  evidence={'releaseClosure':'fixture only; not release qualification'};raw=json.dumps(evidence).encode();(self.root/'closure.json').write_bytes(raw)
  self.inventory={'selection':copy.deepcopy(self.expected),'roots':roots,'unresolved':[],'reviewedBy':'fixture-owner','refreshProcedure':'review changed selected payload/product/chart closure before re-pin','provenance':[{'path':'closure.json','sha256':hashlib.sha256(raw).hexdigest()}]};self.write(refresh_provenance=True)
 def tearDown(self):self.tmp.cleanup()
 def write(self,refresh_provenance=False):
  if refresh_provenance:
   roots=self.inventory['roots'];requirements={u for r in roots for u in r['uses'] if u.startswith(('release:','disk:','guest-cni:','addon:'))}
   source={'references':{'spec':{'tags':[{'from':{'name':r['source']}} for r in roots if any(u.startswith('release:') for u in r['uses'])]}}};raw=json.dumps(source).encode();(self.root/'source-record.json').write_bytes(raw)
   record={'selectionSha256':sha(self.inventory['selection']),'rootsSha256':sha(roots),'unresolved':[],'coverage':{u:[r['source'] for r in roots if u in r['uses']] for u in requirements},'sources':[{'path':'source-record.json','sha256':hashlib.sha256(raw).hexdigest()}]}
   raw=json.dumps(record).encode();(self.root/'closure.json').write_bytes(raw);self.inventory['provenance'][0]['sha256']=hashlib.sha256(raw).hexdigest()
  raw=json.dumps(self.inventory,sort_keys=True).encode();(self.root/'inventory.json').write_bytes(raw);self.cluster.local_registry['cacheProduction']['inventory']['sha256']=hashlib.sha256(raw).hexdigest()
  rp=self.root/'data/clusters/example-management/local-registry.yaml';doc=yaml.safe_load(rp.read_text());doc['spec']=copy.deepcopy(self.cluster.local_registry);rp.write_text(yaml.safe_dump(doc,sort_keys=False))
 def contract(self):
  return load_contract(self.root,self.cluster,self.defaults)
 def ready(self,c):
  return {'stage':'reader','contractSha256':c['sha256'],'verifiedAt':datetime.now(timezone.utc).isoformat(),'readerSecretRef':c['policy']['readerSecretRef'],'trust':copy.deepcopy(c['policy']['producerTrust']),'runtimePrerequisites':copy.deepcopy(c['policy']['runtimePrerequisites']),'images':{dest:{'root':r['source'].rsplit('@',1)[1],'fullBytesVerified':True,'manifests':[{'fixture':True}],'blobs':[{'fixture':True}]} for r in c['roots'] for dest in {reference(c,r['source']),reference(c,r['source'],consumer=False)}}}
 def test_inventory_mapping_and_production_are_deterministic(self):
  c=self.contract();self.assertEqual(c,self.contract());self.cluster.cache_production=c
  resources=production_resources(self.cluster);self.assertEqual(resources,production_resources(self.cluster));target=yaml.safe_load(next(v for k,v in resources.items() if k.endswith('mirrortarget.yaml')));self.assertFalse(target['spec']['insecure']);self.assertEqual(target['spec']['authSecret'],'production-writer')
  vector=next(r for r in c['roots'] if r['scope']=='cache');ref=vector['source'];mapped=reference(c,ref);self.assertIn(vector['cacheRepository'],mapped)
  rendered=rewrite_images({'pod.yaml':yaml.safe_dump({'spec':{'containers':[{'image':ref}]}})},c);self.assertIn(mapped,rendered['pod.yaml'])
 def test_missing_unknown_stale_and_wrong_arch_fail(self):
  cases=[('missing',lambda i:i['roots'].pop()),('unknown',lambda i:i.update(unresolved=['unresolved chart image'])),('stale',lambda i:i['selection'].update(guestPackageRef='changed')),('arch',lambda i:i['roots'][0].update(architecture='aarch64'))]
  for name,mutate in cases:
   with self.subTest(name=name):
    saved=copy.deepcopy(self.inventory);mutate(self.inventory);self.write()
    with self.assertRaises(ValueError):self.contract()
    self.inventory=saved;self.write()
 def test_changed_customer_inventory_changes_destination_and_contract(self):
  first=self.contract();r=next(r for r in self.inventory['roots'] if r['scope']=='cache');r['cacheRepository']='hosted-content/changed';self.write(refresh_provenance=True);second=self.contract();self.assertNotEqual(first['sha256'],second['sha256']);self.assertIn('changed',reference(second,r['source']))
 def test_readiness_matches_phase_current_content_and_configuration(self):
  c=self.contract();self.cluster.cache_production=c;self.cluster.cache_readiness=self.ready(c);require_ready(self.cluster,'workers')
  cached=next(r for r in c['roots'] if r['phase']=='workers');self.cluster.cache_readiness['images'].pop(reference(c,cached['source']));require_ready(self.cluster,'control-planes')
  with self.assertRaisesRegex(ValueError,'missing'):require_ready(self.cluster,'workers')
  for field,value in [('contractSha256','changed'),('verifiedAt',(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()),('readerSecretRef','wrong'),('stage','site')]:
   self.cluster.cache_readiness=self.ready(c);self.cluster.cache_readiness[field]=value
   with self.assertRaises(ValueError):require_ready(self.cluster,'control-planes')
 def test_current_auth_and_source_file_changes_invalidate_readiness_inventory(self):
  c=self.contract();self.cluster.cache_production=c;self.cluster.cache_readiness=self.ready(c)
  self.cluster.cache_readiness['runtimePrerequisites']['readerSecretResourceVersion']='2'
  with self.assertRaisesRegex(ValueError,'prerequisite'):require_ready(self.cluster,'workers')
  p=self.root/'data/clusters/example-management/hcpclusters/example-multus-a.yaml';p.write_text(p.read_text()+'\n# replacement selected input\n')
  doc=yaml.safe_load(p.read_text());doc['spec']['timescapeExporter']['activation']='automatic';p.write_text(yaml.safe_dump(doc,sort_keys=False))
  with self.assertRaisesRegex(ValueError,'stale'):self.contract()
 def test_missing_receipt_blocks_hosted_creation_but_held_can_prepare(self):
  from gitops_renderer.hosted_clusters import _render_hosted_clusters
  self.cluster.cache_production=self.contract();self.cluster.cache_readiness={};self.cluster.hosted_cluster_lifecycle['phase']='control-planes'
  with self.assertRaisesRegex(ValueError,'readiness'): _render_hosted_clusters(self.cluster,self.defaults)
  require_ready(self.cluster,'held')
 def test_trial_custody_and_bootstrap_cycle_are_rejected(self):
  for mutate in [lambda p:p.update(writerSecretRef=p['readerSecretRef']),lambda p:p.update(producerTrust={'mode':'system','insecure':True})]:
   p=self.cluster.local_registry['cacheProduction'];saved=copy.deepcopy(p);mutate(p)
   with self.assertRaises(ValueError):self.contract()
   self.cluster.local_registry['cacheProduction']=saved
  saved_roots=copy.deepcopy(self.inventory['roots']);extra=copy.deepcopy(next(r for r in saved_roots if r['scope']=='cache'));extra['source']=extra['source'].split('@')[0]+'@sha256:'+'0'*64;extra.update(scope='preceding',phase='control-planes');self.inventory['roots'].append(extra);self.write()
  with self.assertRaisesRegex(ValueError,'mix'):self.contract()
  self.inventory['roots']=saved_roots;self.write(refresh_provenance=True)
  disk=next(r for r in self.inventory['roots'] if any(u.startswith('disk:') for u in r['uses']));disk.update(scope='cache',phase='workers');self.write(refresh_provenance=True)
  with self.assertRaisesRegex(ValueError,'precede'):self.contract()
 def test_real_resolver_and_renderer_transition_use_the_same_contract(self):
  import shutil
  from gitops_renderer import render_gitops
  for folder in ('.artifacts','schemas','templates'):
   shutil.copytree(ROOT/folder,self.root/folder)
  p=self.root/'data/clusters/example-management/cluster.yaml';doc=yaml.safe_load(p.read_text());doc['spec']['day2GitOps']['hostedClusterLifecycle']['phase']='held';p.write_text(yaml.safe_dump(doc,sort_keys=False))
  platform=resolve(self.root);c=next(c for c in platform.clusters if c.name=='example-management')
  first=render_gitops(c,platform.fabric.fabric_asn,platform.defaults);self.assertEqual(first,render_gitops(c,platform.fabric.fabric_asn,platform.defaults));self.assertTrue(any(k.endswith('production-imageset.yaml') for k in first))
  c.hosted_cluster_lifecycle['phase']='workers'
  with self.assertRaisesRegex(ValueError,'readiness'):render_gitops(c,platform.fabric.fabric_asn,platform.defaults)
  c.cache_readiness=self.ready(c.cache_production);active=render_gitops(c,platform.fabric.fabric_asn,platform.defaults);self.assertTrue(any('HostedCluster' in value for value in active.values()))
 def test_release_metadata_component_cannot_be_omitted_from_inventory(self):
  source=self.root/'source-record.json';doc=json.loads(source.read_text());doc['references']['spec']['tags'].append({'from':{'name':'upstream.example/omitted@sha256:'+'f'*64}});source.write_text(json.dumps(doc))
  closure=self.root/'closure.json';record=json.loads(closure.read_text());record['sources'][0]['sha256']=hashlib.sha256(source.read_bytes()).hexdigest();closure.write_text(json.dumps(record));self.inventory['provenance'][0]['sha256']=hashlib.sha256(closure.read_bytes()).hexdigest();self.write()
  with self.assertRaisesRegex(ValueError,'release component missing'):self.contract()
 def test_preceding_registry_has_no_cache_dependency(self):
  self.cluster.local_registry['cacheProduction']={'mode':'preceding-registry'};self.assertEqual(load_contract(self.root,self.cluster,self.defaults),{'mode':'preceding-registry'})
if __name__=='__main__':unittest.main()
