#!/usr/bin/env python3
"""Architecture views for the declared compact profile; no live-state claim.

QUESTION: what system is composed, and what contracts connect its domains?
OWNERS: README.md, architectures/FABRIC_NETWORK.md, COMPACT_BAREMETAL_INSTALL.md,
REGISTRY_CHAIN.md; downstream Compute/Airgap/GitOps/Fabric README figures.
SPEC: each view below lists compact device/runtime nodes, environment zones and
explicit network, API, storage, telemetry or artifact connections. Coordinates
are presentation only; network identities/addresses come from maintained YAML.
"""
from pathlib import Path
import yaml
from diagram_theme import write_diagram, COLORS, RELATIONS
ROOT=Path(__file__).resolve().parents[2]
catalog=yaml.safe_load((ROOT/'data/networks/example-federal.yaml').read_text())['spec']
cluster=yaml.safe_load((ROOT/'data/clusters/example-management/cluster.yaml').read_text())['spec']
mgmt=catalog['managementNetworks']['Management'];attach=mgmt['aciAttachment'];fabric=catalog['fabric'];peering=catalog['kubeAPINetworks']['tenant-b-kube-api-peering'];workers=catalog['tenantNetworks']['tenant-b-workers']
mi=cluster['agentConfig']['managementInterface'];ti=cluster['day2GitOps']['parentNetworking']['tenantInterface']
management_leaves=' / '.join(str(n) for n in sorted({p['nodeId'] for p in attach['serverPorts']}))
tenant_leaves=' / '.join(str(p['nodeId']) for p in catalog['fabric']['aci']['borderLeafNodes'])
COLORS.update(cisco='#E6F4FC',openshift='#FFF0EF',storage='#FFF2DC')
def n(id,x,y,w,title,line='',kind='external'):
 return dict(id=id,x=x,y=y,width=w,height=66,kind=kind,title=title,lines=[line] if line else [],line_font_size=16,min_line_font_size=15)
def e(a,b,relation,label='',at=None,via=(),directed=False):
 v=dict(relation=relation,points=[a,*via,b],directed=directed)
 if label:v.update(label=label,label_at=at)
 return v
def z(id,x,y,w,h,label):return dict(id=id,x=x,y=y,width=w,height=h,label=label,fill_opacity=.25)
def draw(name,title,subtitle,nodes,edges,zones,height):
 write_diagram(__file__,name+'.svg',title=title,subtitle=subtitle,description=subtitle+' Declared example architecture, not observed deployment. Labels and adjacent ownership tables are the text alternative.',width=900,height=height+50,zones=zones,nodes=nodes,edges=edges,legend=[])
 path=Path(__file__).with_name(name+'.svg');text=path.read_text();legend=[]
 for index,relation in enumerate(dict.fromkeys(x['relation'] for x in edges)):
  style=RELATIONS[relation];x=40+(index%2)*430;y=height-10+(index//2)*27
  dash=f' stroke-dasharray="{style["dash"]}"' if style['dash'] else ''
  legend.append(f'<line x1="{x}" y1="{y}" x2="{x+34}" y2="{y}" stroke="{style["color"]}" stroke-width="3"{dash}/><text x="{x+44}" y="{y+5}" font-size="14" fill="#344563">{style["label"]}</text>')
 path.write_text(text.replace('</svg>', ''.join(legend)+'</svg>'))
# Physical uplinks lead into the management cluster; hosted CP pods stay inside it.
draw('platform-integration','Secure OpenShift platform on bare metal','Compact profile: fabric, networking, storage, hosted workloads and configuration delivery.',[
 n('services',30,85,290,'Site services','DNS · time · image sources'),n('git',650,85,220,'GitHub','reviewed desired state'),
 n('leaf-m',70,260,220,'Management leaves',management_leaves,'cisco'),n('leaf-t',360,260,230,'Tenant BGP leaves',tenant_leaves,'cisco'),n('apic',660,260,180,'APIC','ACI as code','cisco'),
 n('bond0',70,390,220,mi['bond']+' · management',f"VLAN {mgmt['vlan']} · default route",'cisco'),n('bond1',360,390,230,ti['bond']+' · tenant trunk',f"VLAN {peering['vlan']} / {workers['vlan']}",'cisco'),
 n('hcp',60,530,245,'MCE / HyperShift','hosted control-plane pods','openshift'),n('bgp',360,530,230,'Isovalent Cilium','CNI · policy · BGP','cisco'),n('argo',660,530,180,'Argo CD','management + guests','openshift'),
 n('core',60,670,245,'Management services','API · DNS · registry','openshift'),n('guest',360,670,230,'Hosted OpenShift','KubeVirt worker VMs','openshift'),n('observe',660,670,180,'Hubble / Timescape','flow observability','cisco'),
 n('storage',60,790,530,'Portworx CSI','physical disks → shared persistent volumes','storage'),n('base',60,895,780,'OpenShift Container Platform + Isovalent Cilium','three physical servers · declared NICs · UCS integration optional','openshift')],[
 e((175,151),(175,260),'protocol','DNS / NTP / registry',(175,215)),e((750,151),(750,260),'delivery','fabric config',(750,215),directed=True),e((180,326),(180,390),'protocol',f"VLAN {mgmt['vlan']}",(180,365)),e((475,326),(475,390),'protocol','tagged uplink',(475,365)),
 e((760,151),(840,563),'delivery','application config',(800,475),via=[(885,151),(885,563)],directed=True),
 e((180,456),(60,703),'protocol','node + API access',(135,620),via=[(10,456),(10,703)]),e((475,456),(475,530),'protocol','BGP',(475,502)),e((305,563),(360,703),'api','API / tunnel',(325,632),via=[(330,563),(330,703)]),e((590,703),(590,293),'protocol','direct guest BGP',(625,635),via=[(625,703),(625,293)]),e((475,736),(475,790),'telemetry','PVC / PV',(475,768)),e((750,895),(750,736),'telemetry','flow export',(750,820)),
 ],[z('fabric',20,205,860,150,'ACI fabric'),z('ocp',20,480,860,500,'Management OpenShift')],1015)
# Distinct bootstrap/default route and routed tenant paths, without OSPF dependency.
draw('network-contract','Management access and tenant peering are separate','The example binds declared host interfaces to ACI domains, attachments and routing.',[
 n('site',265,85,370,'Existing site network','DNS / NTP / registry / client routes'),
 n('mgmtleaf',60,235,330,'Management attachment',f"leaves {management_leaves} · VLAN {mgmt['vlan']}",'cisco'),n('tenantleaf',510,235,330,'Tenant routing',f"leaves {tenant_leaves} · AS {fabric['fabricASN']}",'cisco'),
 n('mgtbond',60,405,330,cluster['agentConfig']['managementInterface']['bond'],' + '.join(mi['members'])+' · '+mgmt['cidr'],'cisco'),n('trunk',510,405,330,ti['bond']+' · '+' + '.join(ti['members']),f"{ti['bond']}.{peering['vlan']} · node BGP addresses",'cisco'),
 n('api',60,580,330,'Management API / core services',f"default gateway {mgmt['gateway']}",'openshift'),n('cp',510,580,330,'Management Cilium BGP',f"AS {peering['bgp']['ciliumASN']} · HCP router VIPs",'cisco'),
 n('hcp-pods',60,755,330,'Hosted control-plane pods','management pod network','openshift'),n('vm',510,755,330,'Guest OpenShift workers',f"bridge VLAN {workers['vlan']} · AS {workers['bgp']['ciliumASN']}",'openshift')],[
 e((390,118),(675,235),'protocol','existing site-edge routing',(600,175),via=[(675,118)]),e((265,118),(225,235),'protocol','site management L3Out',(225,175),via=[(225,118)]),e((225,301),(390,438),'protocol','untagged access',(330,345),via=[(225,340),(450,340),(450,438)]),e((675,301),(675,405),'protocol',f"VLAN {peering['vlan']} · eBGP",(675,360)),e((225,471),(225,580),'protocol','bootstrap / service access',(225,530)),e((675,471),(675,580),'protocol','node BGP sessions',(675,530)),e((225,646),(225,755),'api','MCE / HyperShift',(225,705)),e((510,613),(390,788),'api','router Service → pods',(450,705),via=[(450,613),(450,788)]),e((840,788),(840,268),'protocol',f"VLAN {workers['vlan']} · direct guest eBGP",(740,705),via=[(880,788),(880,268)]),
 ],[z('aci',20,185,860,150,'ACI fabric'),z('hosts',20,355,860,485,'Physical OpenShift nodes')],890)
# Install path stays portable; integration with UCS is an option on the boot handoff.
draw('install-lifecycle','From site network to a running OpenShift cluster','Three compact servers: site-owned ISO boot, with UCS automation as an option.',[
 n('services',40,100,340,'Before first boot','network · DNS · time · image access'),n('inputs',520,100,340,'Declared site inputs','hosts · NICs · disks · release'),n('iso',520,290,340,'Compute: Agent ISO','official installer + CLife archive','openshift'),n('hosts',40,290,340,'Three bare-metal servers','site BMC / media; UCS optional'),n('ocp',40,480,340,'OpenShift + Isovalent CNI','nodes Ready · core operators','openshift'),n('argo',520,480,340,'Optional Argo CD bootstrap','reviewed GitOps root + private refs','openshift')],[
 e((690,166),(690,290),'delivery','validate → render → private stage',(690,235),directed=True),e((520,323),(380,323),'delivery','boot ISO',(450,308),directed=True),e((210,166),(210,290),'protocol','working before the cluster exists',(210,235)),e((210,356),(210,480),'delivery','bootstrap',(210,425),directed=True),e((380,513),(520,513),'api','then reconcile applications',(450,450),directed=True)
 ],[],620)
# Image bytes, operator writes and consumers are distinct from registry hosting.
draw('registry-chain','Hosted-image delivery: source, copier, cache and consumer','Management OpenShift boots first, then hosts the selected mirror operator and cache.',[
 n('external',255,90,390,'Vendor image sources','selected releases / products'),
 n('site',255,245,390,'Preceding site registry','available before management boot'),
 n('operator',55,460,330,'Mirror operator','copies the selected inventory','cisco'),
 n('cache',515,460,330,'Hosted-image registry/cache','registry server + persistent storage','openshift'),
 n('workers',515,650,330,'Hosted workers','pull selected cached images','openshift')],[
 e((450,156),(450,245),'supply','mirror selected content',(450,205),directed=True),
 e((600,311),(220,460),'supply','read source',(700,365),via=[(600,440),(220,440)],directed=True),
 e((385,493),(515,493),'supply','copy',(450,477),directed=True),
 e((680,526),(680,650),'supply','image pull',(680,600),directed=True)
 ],[z('hosting',25,390,850,180,'Management OpenShift')],780)
# Git configuration converges through two API destinations, not through VM placement.
draw('gitops-operation','Configuration becomes reconciled cluster objects','Shared inputs → reviewed GitOps roots → Argo CD → management and hosted APIs.',[
 n('dc',40,100,340,'secure-ai-datacenter','validate + generate application values'),n('git',520,100,340,'GitOps repository','bases / charts + generated cluster roots'),n('argo',285,290,330,'Argo CD','reads reviewed repository / ref / path','openshift'),n('mgmt',40,480,340,'Management Kubernetes API','operators · networking · CSI · HCP','openshift'),n('guest',520,480,340,'Registered hosted APIs','guest CNI · policy · applications','openshift')],[
 e((380,133),(520,133),'delivery','reviewed files',(450,118),directed=True),e((690,166),(450,290),'delivery','Git pull',(570,225),via=[(690,245),(450,245)],directed=True),e((350,356),(210,480),'api','management destination',(210,425),via=[(210,356)],directed=True),e((550,356),(690,480),'api','guest destination',(690,425),via=[(690,356)],directed=True)
 ],[],620)

# Reader entrypoints: conceptual composition, then linked implementation views.
# SPEC: solid containment; one neutral solid relation style; no route legend.
# Platform: ACI connects bare metal; OpenShift contains workloads and services.
# Compute: selected inputs -> Agent ISO -> bare metal -> installed OpenShift.
# Airgap: sources -> site registry -> management boot / optional hosted cache.
# GitOps: generated inputs -> repo -> Argo CD -> management / hosted APIs.
# Fabric: reviewed YAML -> Terraform -> APIC -> leaves -> OpenShift servers.
def opening(name, title, nodes, edges, zones=(), height=480):
    import re
    path=write_diagram(__file__, name+'.svg', title=title, subtitle='',
        description=title+'. Conceptual example; configuration detail is linked from the owning README.',
        width=800,height=height,zones=list(zones),nodes=nodes,edges=edges,legend=[])
    svg=path.read_text()
    # Keep shared theme rendering, simplifying only these overview views.
    svg=re.sub(r'<text[^>]*class="(?:heading|subheading)"[^>]*>.*?</text>', '', svg)
    svg=re.sub(r' stroke-dasharray="[^"]*"', '', svg)
    svg=svg.replace('.edge-label{font:650 12.5px', '.edge-label{font:600 18px')
    svg=svg.replace('#4C6FFF', '#5E6C84')
    path.write_text(svg)
def o(id,x,y,w,title,line='',kind='external'):
    v=n(id,x,y,w,title,line,kind)
    v.update(height=72,min_title_font_size=18,line_font_size=18,min_line_font_size=18)
    return v
opening('platform-system','Cisco Secure AI Datacenter platform',[
    o('datacenter',40,16,720,'Cisco Secure AI Datacenter',
      'Composes fabric, install and GitOps configuration','author'),
    o('leaves',240,112,480,'Cisco ACI fabric','Fabric applies the network configuration','cisco'),
    o('apps',160,266,580,'Management apps and services','API · DNS · registry · Argo CD','openshift'),
    o('control-planes',180,400,260,'Control-plane pods','MCE / HyperShift','openshift'),
    o('workers',460,400,260,'KubeVirt worker VMs','Run tenant workloads','openshift'),
    o('isovalent',160,514,580,'Isovalent','Cilium networking · Hubble / Timescape','cisco'),
    o('storage',160,610,580,'Portworx','Shared persistent storage','storage'),
    o('physical',140,716,620,'Cisco UCS servers + disks',
      'Compute provisions UCS and installs OpenShift'),
], [e((240,148),(140,752),'delivery',via=[(70,148),(70,752)]),
    e((450,690),(450,716),'delivery')],
    [z('openshift',140,210,620,480,'Management OpenShift cluster'),
     z('tenants',160,350,580,142,'Nested tenant OpenShift clusters')],height=808)
# Compute: UCS policies configure servers; the Agent ISO installs those same servers.
# Inputs/artifacts and API calls converge at hardware, then hand off to GitOps.
opening('compute-overview','Cisco UCS and OpenShift deployment',[
    o('input',160,16,480,'Datacenter configuration','UCS policies + OpenShift install inputs','author'),
    o('ucs',30,154,330,'Cisco Intersight','UCS policies + server profiles','cisco'),
    o('iso',440,154,330,'OpenShift Agent ISO','Installer + Isovalent CLife archive','openshift'),
    o('servers',160,316,480,'Cisco UCS servers','Configured NICs / disks + ISO boot','cisco'),
    o('ocp',160,442,480,'OpenShift + Isovalent CNI','Bootstrap and readiness checks','openshift'),
    o('gitops',160,568,480,'Argo CD bootstrap','Reviewed GitOps root','openshift'),
],[e((270,88),(195,154),'delivery',via=[(270,122),(195,122)],directed=True),
   e((530,88),(605,154),'delivery',via=[(530,122),(605,122)],directed=True),
   e((195,226),(270,316),'delivery','provision',(190,280),via=[(195,290),(270,290)],directed=True),
   e((605,226),(530,316),'delivery','boot ISO',(620,280),via=[(605,290),(530,290)],directed=True),
   e((400,388),(400,442),'delivery',directed=True),
   e((400,514),(400,568),'delivery',directed=True)],height=666)
opening('airgap-overview','Cisco Secure AI Airgap services and image architecture',[
    o('airgap',20,16,760,'Cisco Secure AI Airgap',
      'Configures site services and mirrors selected images','author'),
    o('sources',20,168,310,'Vendor image sources','Red Hat · Isovalent · NVIDIA'),
    o('registry',400,168,360,'Site registry','Bootstrap + runtime images'),
    o('services',400,270,360,'Shared services','Runners · media · state · S3'),
    o('management',40,510,310,'Management components','Boot + platform images','openshift'),
    o('operator',420,510,340,'Kubernetes mirror operator','Copies selected images','cisco'),
    o('cache',420,626,340,'In-cluster registry/cache','PVC on shared storage','openshift'),
    dict(o('tenants',40,744,720,'Nested tenant OpenShift clusters',
           'HyperShift control-plane pods + KubeVirt worker VMs','openshift'),height=96),
], [e((330,204),(400,204),'supply',directed=True),
    e((400,220),(195,510),'supply',via=[(370,220),(370,486),(195,486)],directed=True),
    e((760,204),(590,510),'supply',via=[(790,204),(790,486),(590,486)],directed=True),
    e((590,582),(590,626),'supply',directed=True),
    e((590,698),(400,744),'supply',via=[(590,720),(400,720)],directed=True)],
    [z('services',380,112,400,250,'Shared external services host'),
     z('management',20,440,760,420,'Management OpenShift cluster')],height=880)
# GitOps: authored resources plus imported selections converge at Argo CD.
# Management owns the hosted CP/VM objects; tenant API receives guest objects.
opening('gitops-overview','Configuration and reconciliation',[
    o('input',20,16,330,'Datacenter configuration','Generated roots + site overlays','author'),
    o('library',450,16,330,'Reusable resources','Application bases + Helm charts'),
    o('repo',160,152,480,'secure-ai-gitops','Reviewed repository / revision / path','author'),
    o('argo',160,282,480,'Argo CD on management','Reads Applications + renders sources','openshift'),
    o('management',40,486,330,'Management API','Platform services + HCP lifecycle','openshift'),
    o('tenant',430,486,330,'Tenant APIs','Guest CNI + policy + workloads','openshift'),
    o('nested',40,610,330,'Control planes + worker VMs','HyperShift pods + KubeVirt VMs','openshift'),
    o('apps',430,610,330,'Tenant Kubernetes objects','Services + Pods + PVCs','openshift'),
],[e((185,88),(280,152),'delivery',via=[(185,120),(280,120)],directed=True),
   e((615,88),(520,152),'delivery',via=[(615,120),(520,120)],directed=True),
   e((400,224),(400,282),'delivery',directed=True),
   e((270,354),(205,486),'delivery','management destination',(205,422),via=[(270,440),(205,440)],directed=True),
   e((530,354),(595,486),'delivery','registered tenant destination',(595,422),via=[(530,440),(595,440)],directed=True),
   e((205,558),(205,610),'delivery',directed=True),
   e((595,558),(595,610),'delivery',directed=True)],
   [z('management-host',20,452,370,250,'Management OpenShift'),
    z('tenant-cluster',410,452,370,250,'Nested tenant OpenShift')],height=722)
# Fabric: policy through APIC; separate management and tenant attachments on hosts.
opening('fabric-overview','ACI configuration and server connectivity',[
    o('yaml',20,16,330,'Network configuration','Management + access + tenants','author'),
    o('terraform',450,16,330,'Terraform / NAC-ACI','Validate + plan + apply'),
    o('apic',450,148,330,'Cisco APIC','Realizes reviewed network policy','cisco'),
    o('leaves',160,300,480,'ACI leaf switches','Server attachments + tenant routing','cisco'),
    o('management',40,456,330,'Management attachment','Management VLAN + gateway','cisco'),
    o('tenant',430,456,330,'Tenant attachment','Trunk VLANs + BGP peering','cisco'),
    o('servers',160,588,480,'UCS / OpenShift servers','Host networking configured by Compute / GitOps','openshift'),
],[e((350,52),(450,52),'delivery',directed=True),
   e((615,88),(615,148),'delivery',directed=True),
   e((615,220),(400,300),'delivery',via=[(615,260),(400,260)],directed=True),
   e((270,372),(205,456),'delivery',via=[(270,412),(205,412)]),
   e((530,372),(595,456),'delivery',via=[(530,412),(595,412)]),
   e((205,528),(270,588),'delivery',via=[(205,560),(270,560)]),
   e((595,528),(530,588),'delivery',via=[(595,560),(530,560)])],
   [z('aci',20,258,760,288,'Cisco ACI fabric')],height=684)
