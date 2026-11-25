"""Add evidence links to the candidate ledger. Run with the raw inventory directory."""
import csv
import gzip
import json
import sys
from collections import Counter
from pathlib import Path
rows=list(csv.DictReader((Path(sys.argv[1])/'obligations.csv').open()))
with (Path(__file__).resolve().parent / 'return-witnesses.csv').open() as f:
    return_edits = {}
    for result in csv.DictReader(f):
        # drop_sum tests a method postcondition with an unchanged successful return.
        if int(result['client_exit']) != 0 and result['variant'] != 'drop_sum':
            return_edits.setdefault(result['symbol'], set()).add(result['variant'])
product = Path(__file__).resolve().parent
table_runs = {}
with (product / 'table-ownership.csv').open() as stream:
    for result in csv.DictReader(stream):
        if tuple(int(result[k]) for k in ('baseline', 'forward', 'borrow', 'extra_owner')) == (0, 0, 11, 12):
            table_runs.setdefault(result['symbol'], set()).add(result['package'])
with (product / 'argument-abi.json').open() as stream:
    argument_runs = json.load(stream)['client_exits']
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
    if kind == 'by_value_return' and row['symbol'] in return_edits:
        row['independent_edit_witness'] = 'return-witnesses.csv'
        row['edit_family'] = '|'.join(sorted(return_edits[row['symbol']]))
    if kind == 'by_value_return' and table_runs.get(row['symbol']) == {'dal-2025.9.0', 'dal-2025.11.0'}:
        row['independent_edit_witness'] = 'table-ownership.csv'
        row['edit_family'] = 'borrow|extra_owner'
    if kind == 'by_value_arg0' and argument_runs.get(row['symbol']) == {'old': 0, 'control': 0, 'mutant': 41}:
        row['independent_edit_witness'] = 'argument-abi.json'
        row['edit_family'] = 'record_call_triviality'
    if kind == 'cxx_method' and row['symbol'] == '_ZN4daal8services10interface16Status5clearEv':
        row['independent_edit_witness'] = 'status-clear.csv'
        row['edit_family'] = 'no_op_clear'
    status_edits = {
        ('constructor_abi_variant', '_ZN4daal8services10interface16StatusC1ERKS2_'): 'discard_copy',
        ('cxx_method', '_ZN4daal8services10interface16StatusaSERKS2_'): 'discard_assignment',
    }
    if (kind, row['symbol']) in status_edits:
        row['independent_edit_witness'] = 'ownership-campaign.csv'
        row['edit_family'] = status_edits[(kind, row['symbol'])]
with gzip.open('ledger.csv.gz','wt') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
mapped={r['symbol'] for r in rows if r['providers']}
imported={r['symbol'] for r in rows if r['providers'] and r['client_imports']}
summary=dict(candidate_rows=len(rows),kinds=counts,provider_matched_names=len(mapped),matched_names_imported=len(imported),rows_with_edit_witness=sum(bool(r['independent_edit_witness']) for r in rows),by_value_return_provider_names=len({r['symbol'] for r in rows if r['kind']=='by_value_return' and r['providers']}))
Path('coverage.json').write_text(json.dumps(summary,indent=2)+'\n')
