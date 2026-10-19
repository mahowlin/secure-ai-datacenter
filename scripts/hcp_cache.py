"""Selected HCP image delivery: offline inventory binding and lifecycle barrier.

No registry client or credential values live here. Inventory closure is supplied
and reviewed by the site; rendered references are checked, never guessed.
"""
from __future__ import annotations
import copy, hashlib, json, re
from datetime import datetime, timezone
from pathlib import Path
import yaml

DIGEST = re.compile(r'sha256:[0-9a-f]{64}')
REPO = re.compile(r'[a-z0-9][a-z0-9._:/-]*')


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def repository(ref):
    ref = ref.split('@')[0]
    return ref.rsplit(':', 1)[0] if ':' in ref.rsplit('/', 1)[-1] else ref


def read_pinned(root, ref):
    path = (root / ref['path']).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('required inventory/provenance file missing or outside source root')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('inventory/provenance file checksum mismatch: ' + ref['path'])
    return json.loads(raw)


def selection(root, cluster, defaults):
    """Bind all maintained inputs; phase/replica/receipt changes are transitions."""
    facts = {}
    for path in sorted((root/'data').rglob('*.yaml')) + [root/'platform-release.yaml']:
        docs = list(yaml.safe_load_all(path.read_text()))
        doc = docs[0] if len(docs) == 1 else docs
        if path.is_relative_to(root/'data/clusters') and not path.is_relative_to(root/f'data/clusters/{cluster.name}'):
            continue
        if path == root/f'data/clusters/{cluster.name}/cluster.yaml':
            lifecycle = doc['spec'].get('day2GitOps', {}).get('hostedClusterLifecycle', {})
            for key in ('phase', 'workerReplicas', 'workerReplicasByCluster'): lifecycle.pop(key, None)
        if path == root/f'data/clusters/{cluster.name}/local-registry.yaml':
            doc['spec'].pop('mirrors',None)
            policy = doc['spec'].get('cacheProduction', {})
            for key in ('inventory', 'readiness'): policy.pop(key, None)
        facts[str(path.relative_to(root))] = sha(doc)
    # Inspect actual source-rendered images, not a historical hand-maintained BOM.
    from gitops_renderer import render_gitops
    probe = copy.deepcopy(cluster)
    probe.cache_production = {}
    probe.local_registry.pop('cacheProduction', None)
    if probe.local_registry.get('externalPull'): probe.local_registry['externalPull']['enabled'] = False
    for h in probe.hosted_clusters: h.timescape_exporter_image_pull = ''
    files = render_gitops(probe, 0, defaults)
    images, preceding_images = set(), set()
    def walk(value, found):
        if isinstance(value, dict):
            image = value.get('image')
            if isinstance(image, str) and '/' in image and '://' not in image: found.add(image)
            if isinstance(value.get('repository'), str) and '/' in value['repository']:
                if value.get('sha'): found.add(value['repository']+'@'+value['sha'])
                elif value.get('tag'): found.add(value['repository']+':'+str(value['tag']))
            for key, child in value.items():
                if key in ('values', 'valuesObject') and isinstance(child, str):
                    walk(yaml.safe_load(child),found)
                else: walk(child,found)
        elif isinstance(value, list):
            for child in value: walk(child,found)
    guest_names={h.name for h in probe.hosted_clusters}
    for path,text in files.items():
        for doc in yaml.safe_load_all(text):
            found=set();walk(doc,found);images.update(found)
            guest_app=isinstance(doc,dict) and doc.get('spec',{}).get('destination',{}).get('name') in guest_names
            hosted_object=isinstance(doc,dict) and doc.get('kind') in ('HostedCluster','NodePool')
            if '/tenant/' not in path and not guest_app and not hosted_object:preceding_images.update(found)
    from gitops_renderer.hosted_clusters import _hcp_release_image
    releases = {h.name: _hcp_release_image(probe, h, defaults) for h in probe.hosted_clusters}
    return {'inputSha256': sha(facts), 'sourceFiles': facts, 'renderedImages': sorted(images), 'precedingImages': sorted(preceding_images),
            'hostedReleases': releases, 'architecture': defaults.gitops_hosted_cluster['releaseImageArchitectureSuffix'],
            'guestPackageRef': cluster.tenant_guest_cni.package_release_ref,
            'guestProduct': {'lifecycle': cluster.tenant_guest_cni.lifecycle,
                'channel': cluster.tenant_guest_cni.channel, 'startingCSV': cluster.tenant_guest_cni.starting_csv,
                'clifeImage': cluster.tenant_guest_cni.clife_image,
                'packageContractSha256': getattr(cluster.tenant_guest_cni.package,'contract_sha256',''),
                'managementReleaseRef': cluster.release_ref,
                'managementCNIversion': cluster.release.cilium_day1_version if cluster.release else ''},
            'guestCNI': sorted(h.name for h in cluster.hosted_clusters if h.network_type == 'Cilium'),
            'addons': sorted(k for k,v in cluster.gitops_addons.items() if v)}


def load_contract(root, cluster, defaults):
    policy = (cluster.local_registry or {}).get('cacheProduction')
    if not policy: return {}
    if policy.get('mode') == 'preceding-registry': return {'mode': 'preceding-registry'}
    if policy.get('mode') != 'hosted-cache': raise ValueError('cacheProduction.mode must be preceding-registry or hosted-cache')
    fields = {'mode','inventory','readiness','siteRegistry','pullRegistry','syncRegistry','readerSecretRef','writerSecretRef','siteWriterSecretRef','producerTrust','runtimePrerequisites','namespace','imageSet','mirrorTarget','repositoryPrefix','caConfigMap','releaseLookup','maxRoots','maxAgeSeconds','concurrency','batchSize','maxRetries','pollInterval','checkExistInterval','resources'}
    missing = fields - {'readiness'} - set(policy)
    if missing or set(policy)-fields: raise ValueError('cacheProduction missing/unknown settings: '+str(sorted(missing or set(policy)-fields)))
    trust = policy['producerTrust']
    if set(trust) != ({'mode'} if trust.get('mode')=='system' else {'mode','path','sha256'}): raise ValueError('explicit system/additional-ca trust settings required; no insecure flag')
    inventory = read_pinned(root, policy['inventory'])
    expected = selection(root, cluster, defaults)
    if inventory.get('selection') != expected: raise ValueError('selected inventory is stale: regenerate selection and review release/disk/product/add-on closure')
    if inventory.get('unresolved') != []: raise ValueError('selected inventory has unknown dependencies')
    provenance = inventory.get('provenance', [])
    if not provenance: raise ValueError('versioned inventory requires provenance/closure evidence')
    provenance_records = [read_pinned(root, ref) for ref in provenance]
    if not inventory.get('reviewedBy') or not inventory.get('refreshProcedure'): raise ValueError('inventory requires completeness reviewer and usable refresh procedure')
    required = {f'release:{h}' for h in expected['hostedReleases']} | {f'disk:{h}' for h in expected['hostedReleases']} | {f'guest-cni:{h}' for h in expected['guestCNI']} | {f'addon:{a}' for a in expected['addons']}
    roots = inventory.get('roots', [])
    if type(policy.get('maxRoots')) is not int or not 0 < len(roots) <= policy['maxRoots']: raise ValueError('empty or over-budget selected inventory')
    seen, destinations, coverage, refs, mapping = set(), set(), set(), set(), {}
    repository_scopes = {}
    for item in roots:
        source = item['source']; digest = source.rsplit('@',1)[1]; repo = repository(source)
        if not DIGEST.fullmatch(digest) or not REPO.fullmatch(repo) or '/' not in repo or repo != source.rsplit('@',1)[0]: raise ValueError('selected source must be a repository and exact digest')
        if source in seen: raise ValueError('duplicate selected source')
        seen.add(source)
        if repository_scopes.setdefault(repo,item['scope'])!=item['scope']:
            raise ValueError('one source repository cannot mix cache and preceding scopes: prefix mirrors are repository-wide')
        for key in ('siteRepository','cacheRepository'):
            if not re.fullmatch(r'[a-z0-9]+(?:[._/-][a-z0-9]+)*',item[key]) or '..' in item[key].split('/'): raise ValueError('invalid destination repository')
        dest = item['cacheRepository']
        if dest in destinations and mapping.get(repo) != dest: raise ValueError('destination collision')
        destinations.add(dest)
        if repo in mapping and mapping[repo] != dest: raise ValueError('one source repository has conflicting mappings')
        if any(r != repo and d == dest for r,d in mapping.items()): raise ValueError('source repositories collide at cache destination')
        mapping[repo]=dest
        if item['phase'] not in ('control-planes','workers') or item['scope'] not in ('preceding','cache'): raise ValueError('unknown root phase or destination scope')
        if item.get('architecture') != expected['architecture']: raise ValueError('selected root architecture mismatch')
        if not isinstance(item.get('uses'),list) or not item['uses']: raise ValueError('selected root requires explicit component uses')
        coverage.update(item['uses']); refs.add(source)
        for ref in item.get('requestedReferences', []):
            if repository(ref) not in (repo, policy['siteRegistry']+'/'+item['siteRepository']): raise ValueError('requested reference differs from source/site mapping')
            if repository_scopes.setdefault(repository(ref),item['scope'])!=item['scope']:
                raise ValueError('consumer repository alias mixes cache/preceding scopes')
            refs.add(ref)
    if required-coverage: raise ValueError('inventory missing selected release/disk/product/add-on requirements: '+str(sorted(required-coverage)))
    if set(expected['renderedImages'])-refs: raise ValueError('inventory missing rendered image references: '+str(sorted(set(expected['renderedImages'])-refs)))
    # Completeness is a versioned owner review, bound to this exact selection/root set.
    reviewed=set()
    for record in provenance_records:
        if not isinstance(record,dict):raise ValueError('closure provenance must be a reviewed record')
        if record.get('selectionSha256')!=sha(expected) or record.get('rootsSha256')!=sha(roots) or record.get('unresolved')!=[]:
            raise ValueError('closure provenance is stale or has unresolved dependencies')
        if not record.get('sources'):raise ValueError('closure provenance requires original release/stream/product/chart source records')
        for source in record['sources']:
            raw=read_pinned(root,source)
            # oc adm release info: every payload component must be represented.
            for tag in (raw.get('references',{}).get('spec',{}).get('tags',[]) if isinstance(raw,dict) else []):
                ref=tag.get('from',{}).get('name','')
                if '@' not in ref or repository(ref)+'@'+ref.rsplit('@',1)[1] not in seen:
                    raise ValueError('selected release component missing from inventory: '+ref)
                if not any(r['source'].rsplit('@',1)[1]==ref.rsplit('@',1)[1] and r['phase']=='control-planes' for r in roots):
                    raise ValueError('release payload component must precede control-plane creation: '+ref)
        for use, members in record.get('coverage',{}).items():
            allowed={r['source'] for r in roots if use in r['uses']}
            if not members or not set(members)<=allowed:raise ValueError('closure coverage differs from selected root uses: '+use)
            reviewed.add(use)
    if required-reviewed:raise ValueError('closure provenance does not cover every selected requirement')
    # Named bootstrap infrastructure must remain reachable from preceding tier.
    infrastructure = set(expected['precedingImages']) | set(cluster.local_registry['images'].values()) | {i['source'] for i in cluster.local_registry.get('ocMirror',{}).get('release',{}).get('bootstrapImages',[])}
    for item in roots:
        if any(u.startswith(('release:','disk:')) for u in item['uses']) and item['phase']!='control-planes':
            raise ValueError('release lookup and management CDI disk content must precede control-plane creation')
        if infrastructure.intersection([item['source'], *item.get('requestedReferences',[])]) and (item['scope'] != 'preceding' or item['phase'] != 'control-planes'):
            raise ValueError('management/registry/operator bootstrap must use preceding-registry content before control-plane creation')
    alias_map={}
    for item in roots:
        if item['scope']!='cache':continue
        for alias in {repository(item['source']),*(repository(r) for r in item.get('requestedReferences',[]))}:
            if alias_map.setdefault(alias,item['cacheRepository'])!=item['cacheRepository']:
                raise ValueError('consumer repository alias has conflicting destination maps')
    for name, release in expected['hostedReleases'].items():
        if not any(release in r.get('requestedReferences', []) and 'release:'+name in r['uses'] for r in roots):
            raise ValueError('release payload pin/coverage differs from selected HostedCluster')
    for endpoint in ('siteRegistry','pullRegistry','syncRegistry'):
        if not re.fullmatch(r'[a-z0-9][a-z0-9.:-]*',policy[endpoint]): raise ValueError('registry must be an authority without URL, credentials or path')
    if policy['siteRegistry'] != defaults.registry_url: raise ValueError('production site registry differs from selected defaults')
    if policy['readerSecretRef'] == policy['writerSecretRef'] or policy['readerSecretRef'] == policy['siteWriterSecretRef']: raise ValueError('reader and writer Secret refs must be separate')
    for key in ('readerSecretRef','writerSecretRef','siteWriterSecretRef','caConfigMap','namespace','imageSet','mirrorTarget'):
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]*',policy[key]): raise ValueError('invalid production object/credential reference')
    if policy['releaseLookup'] not in ('preceding-registry','hosted-cache'): raise ValueError('explicit releaseLookup required')
    if policy['releaseLookup']=='preceding-registry':
        for release in expected['hostedReleases'].values():
            if not any(release in r.get('requestedReferences',[]) and r['scope']=='preceding' for r in roots):
                raise ValueError('preceding release lookup conflicts with repository mirror scope')
    if policy['releaseLookup']=='hosted-cache':
        for release in expected['hostedReleases'].values():
            if not any(release in r.get('requestedReferences',[]) and r['scope']=='cache' and r['phase']=='control-planes' for r in roots):
                raise ValueError('cached release lookup must be available before control-plane creation')
    if policy['producerTrust']['mode'] not in ('system','additional-ca'): raise ValueError('normal TLS is required')
    if policy['producerTrust']['mode']=='additional-ca': read_pem(root, policy['producerTrust'])
    for field in ('maxAgeSeconds','concurrency','batchSize','maxRetries'):
        if type(policy[field]) is not int or policy[field]<1: raise ValueError('explicit positive production '+field+' required')
    from gitops_renderer.registry_exposure import registry_exposure
    from gitops_renderer.selected_inputs import selected
    with selected(defaults):
        exposure = registry_exposure(cluster)
    if not exposure or policy['pullRegistry'] != exposure['endpoint'] or not exposure['subjects'] or not set(exposure['subjects']) <= set(expected['hostedReleases']):
        raise ValueError('production reader endpoint/subjects differ from declared consumer frontend')
    if policy['readerSecretRef'] != exposure['consumer']['pullSecret'] or policy['syncRegistry'] != f"{cluster.local_registry['syncService']}.{cluster.local_registry['namespace']}.svc:{cluster.local_registry['port']}":
        raise ValueError('production reader/writer binding differs from registry deployment')
    if not isinstance(policy.get('runtimePrerequisites'),dict) or not all(policy['runtimePrerequisites'].get(k) for k in ('readerSecretUID','readerSecretResourceVersion','dnsEvidence','tlsEvidence')):
        raise ValueError('current runtime DNS/TLS/auth prerequisite identities must be declared')
    contract = {'format':'hcp-selected-cache/v1','cluster':cluster.name,'selection':expected,'subjects':sorted(exposure['subjects']),'inventorySha256':policy['inventory']['sha256'],'policy':{k:v for k,v in policy.items() if k not in ('inventory','readiness')},'roots':roots}
    contract['sha256']=sha(contract)
    probe=copy.copy(cluster);probe.cache_production=contract
    production_resources(probe)
    mirrors={}
    for item in roots:
        endpoint=policy['pullRegistry'] if item['scope']=='cache' else policy['siteRegistry']
        destination=item['cacheRepository'] if item['scope']=='cache' else item['siteRepository']
        for source in {repository(item['source']),*(repository(r) for r in item.get('requestedReferences',[]))}:
            if source==endpoint+'/'+destination:continue
            if source in mirrors and mirrors[source]!=(endpoint,destination):raise ValueError('consumer alias has conflicting prefix mirrors')
            mirrors[source]=(endpoint,destination)
    cluster.local_registry['mirrors']=[{'source':source,'endpoint':endpoint,'repository':destination} for source,(endpoint,destination) in sorted(mirrors.items())]
    return contract


def read_pem(root, trust):
    p=(root/trust['path']).resolve()
    if not p.is_relative_to(root.resolve()) or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=trust['sha256'] or b'-----BEGIN CERTIFICATE-----' not in p.read_bytes(): raise ValueError('declared public CA missing or mismatched')
    return str(p)


def reference(contract, source, consumer=True):
    policy=contract['policy']
    matches=[]
    for item in contract['roots']:
        digest=item['source'].rsplit('@',1)[1]
        aliases=[item['source'],*item.get('requestedReferences',[]),
                 policy['siteRegistry']+'/'+item['siteRepository']+'@'+digest,
                 policy['pullRegistry']+'/'+item['cacheRepository']+'@'+digest]
        if source in aliases:matches.append(item)
    if len(matches)!=1:raise ValueError('unknown/ambiguous selected dependency '+source)
    item=matches[0];cached=consumer and item['scope']=='cache'
    return f"{policy['pullRegistry'] if cached else policy['siteRegistry']}/{item['cacheRepository'] if cached else item['siteRepository']}@{item['source'].rsplit('@',1)[1]}"


def require_ready(cluster, phase, now=None):
    contract=cluster.cache_production
    if not contract or contract.get('mode')=='preceding-registry' or phase=='held': return
    if sha({k:v for k,v in contract.items() if k!='sha256'}) != contract['sha256']:
        raise ValueError('resolved cache configuration changed after inventory binding')
    receipt=cluster.cache_readiness
    now=now or datetime.now(timezone.utc)
    if receipt.get('stage') != 'reader' or receipt.get('contractSha256')!=contract['sha256']: raise ValueError('cache readiness missing or selection/destination/config changed')
    checked=datetime.fromisoformat(receipt['verifiedAt'].replace('Z','+00:00'))
    if checked.tzinfo is None or not 0 <= (now-checked).total_seconds() <= contract['policy']['maxAgeSeconds']: raise ValueError('cache readiness stale or future-dated')
    if receipt.get('readerSecretRef')!=contract['policy']['readerSecretRef'] or receipt.get('trust')!=contract['policy']['producerTrust']: raise ValueError('current reader auth/trust prerequisite differs')
    needed=contract['roots'] if phase in ('workers','legacy') else [r for r in contract['roots'] if r['phase']=='control-planes']
    results=receipt.get('images',{})
    for item in needed:
        for dest in {reference(contract,item['source']),reference(contract,item['source'],consumer=False)}:
            value=results.get(dest,{})
            if value.get('root')!=item['source'].rsplit('@',1)[1] or value.get('fullBytesVerified') is not True or not value.get('manifests') or not value.get('blobs'): raise ValueError('required image content is missing/mismatched/unreadable: '+dest)
    if receipt.get('runtimePrerequisites') != contract['policy']['runtimePrerequisites']: raise ValueError('DNS/TLS/auth prerequisite identity changed')


def production_resources(cluster):
    """Existing operator owns these CR kinds; bootstrap trial is not widened."""
    contract=cluster.cache_production
    if not contract.get('roots'): return {}
    from gitops_renderer.oc_mirror_trial import _manifest,_resources
    policy=contract['policy'];operator=cluster.local_registry.get('ocMirror',{})
    if not operator.get('publish') or policy['namespace']!=operator['namespace']:
        raise ValueError('production requires the selected operator OLM owner namespace')
    if policy['writerSecretRef']==operator['authSecret'] or policy['imageSet']==operator['imageSet'] or policy['mirrorTarget']==operator['mirrorTarget']:
        raise ValueError('production must not silently widen trial objects or writer custody')
    prefix=policy['repositoryPrefix']
    if not re.fullmatch(r'[a-z0-9]+(?:[._/-][a-z0-9]+)*',prefix) or operator['targetPrefix'].endswith('/'+prefix):raise ValueError('production requires a distinct explicit repository prefix')
    images=[]
    for item in contract['roots']:
        if item['scope']!='cache':continue
        if not item['cacheRepository'].startswith(prefix+'/'):raise ValueError('cache root escapes production writer prefix')
        images.append({'name':reference(contract,item['source'],consumer=False),'targetRepo':item['cacheRepository'][len(prefix)+1:],'targetTag':item['source'].rsplit('@',1)[1].replace(':','-',1)})
    if not images:raise ValueError('hosted-cache production must select cached image roots')
    target={'apiVersion':'mirror.openshift.io/v1alpha1','kind':'MirrorTarget','metadata':{'name':policy['mirrorTarget'],'namespace':policy['namespace']},'spec':{'registry':policy['syncRegistry']+'/'+prefix,'authSecret':policy['writerSecretRef'],'insecure':False,'caBundle':{'configMapName':policy['caConfigMap'],'key':'ca-bundle.crt'},'imageSets':[policy['imageSet']],'expose':{'type':'Service'},'concurrency':policy['concurrency'],'batchSize':policy['batchSize'],'maxRetries':policy['maxRetries'],'pollInterval':policy['pollInterval'],'checkExistInterval':policy['checkExistInterval'],'manager':{'resources':_resources(policy['resources'],'manager')},'worker':{'resources':_resources(policy['resources'],'worker')}}}
    imageset={'apiVersion':'mirror.openshift.io/v1alpha1','kind':'ImageSet','metadata':{'name':policy['imageSet'],'namespace':policy['namespace']},'spec':{'mirror':{'additionalImages':images}}}
    base=f'apps/hcp/management/oc-mirror-trial/overlays/{cluster.name}'
    return {f'{base}/production-imageset.yaml':_manifest(imageset),f'{base}/production-mirrortarget.yaml':_manifest(target)}


def rewrite_images(files, contract):
    """Pin rendered image consumers to their one selected source/destination map."""
    def image(ref,cached):
        return reference(contract,ref,consumer=cached)
    def walk(value, release=False,cached=True):
        if isinstance(value,dict):
            if isinstance(value.get('image'),str) and '/' in value['image'] and not release:
                value['image']=image(value['image'],cached)
            if isinstance(value.get('repository'),str) and '/' in value['repository'] and (value.get('sha') or value.get('tag')):
                ref=value['repository']+('@'+value['sha'] if value.get('sha') else ':'+str(value['tag']))
                repo,digest=image(ref,cached).rsplit('@',1);value['repository']=repo;value['sha']=digest;value.pop('tag',None)
            for key,child in value.items():
                if key=='values' and isinstance(child,str):
                    obj=yaml.safe_load(child);walk(obj,cached=cached);value[key]=yaml.safe_dump(obj,sort_keys=False)
                else:walk(child,release=key=='release',cached=cached)
        elif isinstance(value,list):
            for child in value:walk(child,cached=cached)
    result={}
    for path,text in files.items():
        docs=list(yaml.safe_load_all(text))
        for doc in docs:
            subject=doc.get('spec',{}).get('destination',{}).get('name') if isinstance(doc,dict) else None
            if subject not in contract['selection']['hostedReleases']:
                subject=next((name for name in contract['selection']['hostedReleases'] if name in path.split('/')),None)
            cached=not subject or subject in contract['subjects']
            walk(doc,cached=cached)
        result[path]='# Generated by render-gitops.py — do not edit manually.\n---\n'+yaml.safe_dump_all(docs,sort_keys=False)
    return result
