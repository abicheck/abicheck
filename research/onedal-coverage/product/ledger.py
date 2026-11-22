"""Add evidence links to the candidate ledger. Run with the raw inventory directory."""
import csv
import gzip
import json
import sys
from collections import Counter
from pathlib import Path
rows=list(csv.DictReader((Path(sys.argv[1])/'obligations.csv').open()))
counts=Counter()
for row in rows:
    kind=row['kind']
    counts[kind]+=1
    header=row['header']
    key=row['key']
    witness=''
    if kind=='field_decl':
        if header=='dal/algorithms/tree_utils/tree_utils.h' and any(t in key for t in ('@S@NodeDescriptor@FI','@S@SplitNodeDescriptor@FI')):
            witness='../pilot/results.csv'
        if header=='dal/algorithms/tree_utils/tree_utils_regression.h' and '@S@LeafNodeDescriptor@FI' in key:
            witness='../pilot/results.csv'
        if header=='oneapi/dal/common.hpp' and '@S@range@FI' in key:
            witness='contracts.csv'
    if kind=='virtual_slot' and header=='dal/algorithms/tree_utils/tree_utils.h' and row['detail'] in ('onLeafNode','onSplitNode'):
        witness='visitor.csv'
    row['independent_edit_witness']=witness
    row['edit_family']=('virtual_reorder' if kind=='virtual_slot' else 'struct_swap_fields') if witness else ''
    if kind == 'cxx_method' and row['symbol'] == '_ZN4daal8services10interface16Status5clearEv':
        row['independent_edit_witness'] = 'status-clear.csv'
        row['edit_family'] = 'no_op_clear'
with gzip.open('ledger.csv.gz','wt') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
mapped={r['symbol'] for r in rows if r['providers']}
imported={r['symbol'] for r in rows if r['providers'] and r['client_imports']}
summary=dict(candidate_rows=len(rows),kinds=counts,provider_matched_names=len(mapped),matched_names_imported=len(imported),rows_with_edit_witness=sum(bool(r['independent_edit_witness']) for r in rows),by_value_return_provider_names=len({r['symbol'] for r in rows if r['kind']=='by_value_return' and r['providers']}))
Path('coverage.json').write_text(json.dumps(summary,indent=2)+'\n')
