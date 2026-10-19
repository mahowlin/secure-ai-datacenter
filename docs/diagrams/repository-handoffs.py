#!/usr/bin/env python3
"""Repository handoffs, owned by README.md.

QUESTION: Which repository turns site inputs into install and application configuration?
TRUTH: artifact/execution responsibilities, not an installed topology.
NODES: site input; secure-ai-airgap; secure-ai-datacenter; secure-ai-fabric;
       secure-ai-compute; secure-ai-gitops; ACI; OpenShift.
EDGES: input -> Datacenter; Datacenter -> Compute and GitOps; Compute -> OpenShift;
       GitOps -> Argo CD in OpenShift; optional Airgap -> installer image supply.
No addresses, release selection or deployment facts are introduced by this view.
"""
from pathlib import Path
from xml.sax.saxutils import escape
from diagram_theme import COLORS, RELATIONS, _edge_path, _edge_label, _node

NODES = [
 dict(id='airgap',x=30,y=85,width=240,height=70,kind='external',title='secure-ai-airgap',lines=['services + image supply']),
 dict(id='inputs',x=630,y=85,width=240,height=70,kind='author',title='Your site inputs',lines=['hosts · networks · release']),
 dict(id='datacenter',x=330,y=245,width=240,height=70,kind='resolve',title='secure-ai-datacenter',lines=['inputs → generated files']),
 dict(id='fabric',x=30,y=390,width=240,height=70,kind='target',title='secure-ai-fabric',lines=['NAC-ACI / Terraform']),
 dict(id='compute',x=330,y=390,width=240,height=70,kind='target',title='secure-ai-compute',lines=['UCS + OpenShift']),
 dict(id='gitops',x=630,y=390,width=240,height=70,kind='target',title='secure-ai-gitops',lines=['Argo CD configuration']),
 dict(id='aci',x=30,y=550,width=240,height=70,kind='runtime',title='ACI fabric',lines=['management + tenant paths']),
 dict(id='cluster',x=330,y=550,width=540,height=70,kind='runtime',title='OpenShift + Argo CD',lines=['installed cluster and reconciled applications']),
]
for item in NODES:item.update(line_font_size=16,min_line_font_size=15,min_title_font_size=16)
EDGES = [
 dict(relation='delivery',points=[(750,155),(750,205),(450,205),(450,245)],directed=True,label='configuration',label_at=(600,200)),
 dict(relation='delivery',points=[(450,315),(450,350),(150,350),(150,390)],directed=True,label='fabric YAML',label_at=(150,335)),
 dict(relation='delivery',points=[(450,315),(450,390)],directed=True,label='UCS + install data',label_at=(450,335)),
 dict(relation='delivery',points=[(450,315),(450,350),(750,350),(750,390)],directed=True,label='app manifests',label_at=(750,335)),
 dict(relation='delivery',points=[(150,460),(150,550)],directed=True,label='Terraform',label_at=(150,515)),
 dict(relation='delivery',points=[(450,460),(450,550)],directed=True,label='install',label_at=(450,515)),
 dict(relation='api',points=[(750,460),(750,550)],directed=True,label='Argo CD syncs',label_at=(750,515)),
 dict(relation='supply',points=[(270,120),(300,120),(300,425),(330,425)],directed=True,label='images',label_at=(300,230)),
]
parts=['<svg xmlns="http://www.w3.org/2000/svg" width="900" height="700" viewBox="0 0 900 700" role="img" aria-labelledby="title description">',
       '<title id="title">Repository handoffs</title>',
       '<desc id="description">secure-ai-datacenter renders fabric YAML for secure-ai-fabric, UCS/install data for secure-ai-compute and application manifests for secure-ai-gitops. Fabric configures ACI; existing NAC repositories can consume the same YAML. Compute installs OpenShift and bootstraps Argo CD; Argo CD synchronizes the GitOps root. Optional Airgap supplies registry images to installation. Existing site services can replace Airgap.</desc>', '<defs>']
for name in ('delivery','api','supply'):
    color=RELATIONS[name]['color']
    parts.append(f'<marker id="arrow-{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10Z" fill="{color}"/></marker>')
parts += ['<style>.node-title{font:700 20px system-ui;fill:#172B4D}.node-line{font:500 18px system-ui;fill:#344563}.edge-label{font:600 18px system-ui;fill:#344563}</style></defs>',
          '<rect width="900" height="700" fill="#F7F9FC"/>', '<text x="24" y="37" font-family="system-ui" font-size="24" font-weight="700" fill="#172B4D">Repository handoffs</text>', '<g id="connections">']
parts += [_edge_path(edge) for edge in EDGES];parts.append('</g><g id="nodes">')
for node in NODES:parts += _node(node)
parts.append('</g><g id="connection-labels">')
for edge in EDGES:parts += _edge_label(edge)
parts += ['</g>', '<text x="24" y="660" font-family="system-ui" font-size="17" fill="#344563">Solid arrows: configuration or reconciliation</text>',
          '<text x="24" y="685" font-family="system-ui" font-size="17" fill="#344563">Dotted arrow: optional image supply</text>', '</svg>']
Path(__file__).with_suffix('.svg').write_text('\n'.join(parts)+'\n')
