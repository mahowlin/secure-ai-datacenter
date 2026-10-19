#!/usr/bin/env python3
"""Export selected inventory requirements or the validated cache producer contract."""
import argparse,json
from pathlib import Path
from resolver import resolve
from hcp_cache import selection,load_contract,sha

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--cluster',required=True)
    group=p.add_mutually_exclusive_group()
    group.add_argument('--review-template',type=Path,help='Inventory JSON to describe an UNREVIEWED closure record')
    group.add_argument('--selection',action='store_true');p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.review_template:
        inventory=json.loads(a.review_template.read_text());roots=inventory['roots']
        uses=sorted({u for root in roots for u in root['uses']})
        value={'selectionSha256':sha(inventory['selection']),'rootsSha256':sha(roots),
               'coverage':{u:[root['source'] for root in roots if u in root['uses']] for u in uses},
               'sources':[],'unresolved':['Complete source/closure review before accepting this record']}
        a.output.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n');return
    platform=resolve(a.root,load_cache=False)
    cluster=next(c for c in platform.clusters if c.name==a.cluster)
    value=selection(a.root,cluster,platform.defaults) if a.selection else load_contract(a.root,cluster,platform.defaults)
    if not a.selection and not value.get('roots'):raise ValueError('select explicit hosted-cache production settings/inventory before exporting')
    a.output.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')
if __name__=='__main__':main()
