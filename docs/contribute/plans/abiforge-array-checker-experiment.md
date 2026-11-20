# Array selector versus public checker

Research artifact for the pinned revision documented in the [plan](abiforge-space-reduction-research.md).

Save the following files under `research/layout-cutoff-pilot/array-vector/` in that checkout. The selector source `run.py` is in the plan. Run `python research/layout-cutoff-pilot/array-vector/checker.py`, then `python research/layout-cutoff-pilot/array-vector/audit.py`. Requires installed abicheck and GCC. Generated libraries and reports are regenerated, not committed.

## checker.py

```python
"""Compare dense and affine-selected lengths using compiler probes and public CLI."""
import ast,json,subprocess,sys,time,hashlib
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
# Load pure selector functions, without rerunning the model experiment.
ns={};tree=ast.parse((HERE/'run.py').read_text());exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('up','obs','diff','bits','reps')],type_ignores=[]),'selector','exec'),ns)
profiles=[('late',('char pad[100];char x;',101,1,100),('char x;char pad[101];',102,1,0)),('identity',('char pad[100];char x;',101,1,100),('char pad[100];char x;',101,1,100)),('grow8',('char pad[8];double x;',16,8,8),('char pad[8];double x;char added;',24,8,8)),('grow1',('char x;',1,1,0),('char x;char added;',2,1,0))]
def cmd(args,out,ok=(0,)):
 t=time.perf_counter();r=subprocess.run(list(map(str,args)),capture_output=True,text=True,cwd=ROOT,timeout=180)
 with (out/'commands.jsonl').open('a') as f:f.write(json.dumps({'argv':list(map(str,args)),'returncode':r.returncode,'seconds':time.perf_counter()-t,'stderr':r.stderr[-2000:]})+'\n')
 assert r.returncode in ok,r.stderr[-3000:]
 return r.stdout
def run(name,cases):
 out=HERE/name;out.mkdir(exist_ok=True);(out/'commands.jsonl').write_text('');start=time.perf_counter();observed={};extract=[]
 for side in ('old','new'):
  h=[];lib=[];probe=['#include <stddef.h>','#include <stdio.h>',f'#include "{side}.h"','int main(void){']
  for i,(pi,n) in enumerate(cases):
   p=profiles[pi];decl,s,a,o=p[1 if side=='old' else 2]
   h += [f'struct I{i}{{{decl}}};',f'struct O{i}{{struct I{i} items[{n}];char tail;}};',f'int use{i}(struct O{i} *p);']
   lib += [f'int use{i}(struct O{i} *p){{return (int)p->items[0].x;}}']
   probe += [f'printf("{i} %zu %zu %zu %zu %zu %zu\\n",sizeof(struct I{i}),offsetof(struct I{i},x),sizeof(struct O{i}),offsetof(struct O{i},items),offsetof(struct O{i},tail),offsetof(struct O{i},items[{n-1}].x));']
  probe+=['return 0;}'];(out/f'{side}.h').write_text('\n'.join(h));(out/f'{side}.c').write_text(f'#include "{side}.h"\n'+'\n'.join(lib));(out/f'{side}-probe.c').write_text('\n'.join(probe))
  cmd(['gcc','-std=c11','-g','-O0','-fPIC','-shared',out/f'{side}.c','-o',out/f'{side}.so'],out)
  cmd(['gcc',out/f'{side}-probe.c','-o',out/f'{side}-probe'],out)
  raw=cmd([out/f'{side}-probe'],out);(out/f'{side}-offsets.txt').write_text(raw);rows=[list(map(int,l.split())) for l in raw.splitlines()];observed[side]=rows
  cmd([sys.executable,'-m','abicheck','dump',out/f'{side}.so','-o',out/f'{side}.json'],out)
  dump=json.loads((out/f'{side}.json').read_text());types={t['name']:t for t in dump['sections']['types']['payload']['types']}
  for i,s,x,sz,items,tail,last in rows:
   pi,n=cases[i];_,ss,aa,oo=profiles[pi][1 if side=='old' else 2]
   want=ns['obs'](ss,aa,oo,0,1,n)
   assert [s,sz,items,tail,last]==[want[0],want[4],want[2],want[3],want[6]]
   for rec,field,value in [(f'I{i}','x',x),(f'O{i}','items',items),(f'O{i}','tail',tail)]:
    fields={f['name']:f['offset_bits'] for f in types[rec]['fields']}
    if fields.get(field)!=value*8:extract.append([side,rec,field,value,fields.get(field)])
 cmd([sys.executable,'-m','abicheck','compare',out/'old.json',out/'new.json','-o',f'json={out/"report.json"}'],out,(0,2,4))
 report=json.loads((out/'report.json').read_text());expected={};got={};duplicates=[]
 for old,new in zip(observed['old'],observed['new']):
  i=old[0]
  for rec,field,index in [(f'I{i}','x',2),(f'O{i}','items',4),(f'O{i}','tail',5)]:
   if old[index]!=new[index]:expected[rec+'::'+field]=[8*old[index],8*new[index]]
 for c in report['changes']:
  if c['kind'] not in ('type_field_offset_changed','struct_field_offset_changed'):continue
  k=c['symbol']+'::'+str(c.get('field_name'));factor=8 if c['kind']=='struct_field_offset_changed' else 1
  if k in got:duplicates.append(k)
  got[k]=[factor*int(c['old_value']),factor*int(c['new_value'])]
 # Other common fields (pad) are outside this projection.
 got={k:v for k,v in got.items() if k.endswith(('::x','::items','::tail'))}
 result={'cases':len(cases),'direct_offset_checks':3*len(cases),'moved':len(expected),'unchanged':3*len(cases)-len(expected),'missing':sorted(expected.keys()-got.keys()),'unexpected':sorted(got.keys()-expected.keys()),'wrong_values':[k for k in expected.keys()&got.keys() if expected[k]!=got[k]],'extraction_mismatches':extract,'duplicates':duplicates,'seconds':time.perf_counter()-start}
 (out/'cases.json').write_text(json.dumps(cases));(out/'summary.json').write_text(json.dumps(result,indent=2));return result
selected=[]
for pi,p in enumerate(profiles):
 pair=(p[1][1:],p[2][1:],0,1)
 for r,v in ns['reps'](pair).items():
  selected += [(pi,r+8*k) for k in v['roots']+[v['other']] if r+8*k<=128]
selected=sorted(set(selected));dense=[(pi,n) for pi in range(len(profiles)) for n in range(1,129)]
# Same bounded domain: equality vectors witnessed by selected and dense sets.
for pi,p in enumerate(profiles):
 pair=(p[1][1:],p[2][1:],0,1)
 assert {tuple(ns['bits'](pair,n)) for i,n in dense if i==pi}=={tuple(ns['bits'](pair,n)) for i,n in selected if i==pi}
result={'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'selected_lengths':selected,'dense':run('dense-checker',dense),'selected':run('selected-checker',selected)}
(HERE/'checker-results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))

```

## audit.py

```python
"""Audit size findings and reconstruct indexed offsets from saved public dumps."""
import json
from pathlib import Path
P=Path(__file__).resolve().parent
results={}
for suite in ('dense-checker','selected-checker'):
 p=P/suite;cases=json.loads((p/'cases.json').read_text());facts={};probes={};bad=[];unknown=0
 for side in ('old','new'):
  facts[side]={t['name']:t for t in json.loads((p/f'{side}.json').read_text())['sections']['types']['payload']['types']}
  probes[side]=[list(map(int,l.split())) for l in (p/f'{side}-offsets.txt').read_text().splitlines()]
  for i,s,x,sz,start,tail,last in probes[side]:
   inner=facts[side][f'I{i}'];outer=facts[side][f'O{i}'];fs={f['name']:f['offset_bits'] for f in inner['fields']};fo={f['name']:f['offset_bits'] for f in outer['fields']};n=cases[i][1]
   actual=[inner['size_bits'],outer['size_bits'],fo['items']+fs['x'],fo['items']+(n-1)*inner['size_bits']+fs['x']]
   expected=[8*s,8*sz,8*(start+x),8*last]
   if actual!=expected:bad.append([side,i,actual,expected])
   unknown+=inner['alignment_bits'] is None
 expected={}
 for old,new in zip(probes['old'],probes['new']):
  i=old[0]
  for rec,index in [(f'I{i}',1),(f'O{i}',3)]:
   if old[index]!=new[index]:expected[rec]=[8*old[index],8*new[index]]
 got={};duplicates=[]
 for c in json.loads((p/'report.json').read_text())['changes']:
  if c['kind']!='type_size_changed':continue
  if c['symbol'] in got:duplicates.append(c['symbol'])
  got[c['symbol']]=[int(c['old_value']),int(c['new_value'])]
 result={'size_checks':2*len(cases),'changed_sizes':len(expected),'missing_sizes':sorted(expected.keys()-got.keys()),'unexpected_sizes':sorted(got.keys()-expected.keys()),'wrong_sizes':[k for k in got.keys()&expected.keys() if got[k]!=expected[k]],'duplicate_sizes':duplicates,'dump_size_and_indexed_offset_errors':bad,'alignment_unavailable_side_records':unknown,'derived_first_last_side_checks':4*len(cases)}
 results[suite]=result
 assert not any(result[k] for k in ('missing_sizes','unexpected_sizes','wrong_sizes','duplicate_sizes','dump_size_and_indexed_offset_errors'))
(P/'audit-results.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))

```

## checker-results.json

```json
{
  "commit": "b0b8793989c150a5b05f48e4e999a7df4219365a",
  "source_sha256": "d6e251525eb860032487c72578dde2b6ba0fdc72fa27696b265b2657368f85f0",
  "selected_lengths": [
    [
      0,
      1
    ],
    [
      0,
      2
    ],
    [
      0,
      3
    ],
    [
      0,
      4
    ],
    [
      0,
      5
    ],
    [
      0,
      6
    ],
    [
      0,
      7
    ],
    [
      0,
      8
    ],
    [
      0,
      101
    ],
    [
      1,
      1
    ],
    [
      1,
      2
    ],
    [
      1,
      3
    ],
    [
      1,
      4
    ],
    [
      1,
      5
    ],
    [
      1,
      6
    ],
    [
      1,
      7
    ],
    [
      1,
      8
    ],
    [
      2,
      1
    ],
    [
      2,
      2
    ],
    [
      2,
      3
    ],
    [
      2,
      4
    ],
    [
      2,
      5
    ],
    [
      2,
      6
    ],
    [
      2,
      7
    ],
    [
      2,
      8
    ],
    [
      2,
      9
    ],
    [
      3,
      1
    ],
    [
      3,
      2
    ],
    [
      3,
      3
    ],
    [
      3,
      4
    ],
    [
      3,
      5
    ],
    [
      3,
      6
    ],
    [
      3,
      7
    ],
    [
      3,
      8
    ],
    [
      3,
      9
    ]
  ],
  "dense": {
    "cases": 512,
    "direct_offset_checks": 1536,
    "moved": 512,
    "unchanged": 1024,
    "missing": [],
    "unexpected": [],
    "wrong_values": [],
    "extraction_mismatches": [],
    "duplicates": [],
    "seconds": 9.58828313200138
  },
  "selected": {
    "cases": 35,
    "direct_offset_checks": 105,
    "moved": 36,
    "unchanged": 69,
    "missing": [],
    "unexpected": [],
    "wrong_values": [],
    "extraction_mismatches": [],
    "duplicates": [],
    "seconds": 3.6179501959995832
  }
}
```

## audit-results.json

```json
{
  "dense-checker": {
    "size_checks": 1024,
    "changed_sizes": 768,
    "missing_sizes": [],
    "unexpected_sizes": [],
    "wrong_sizes": [],
    "duplicate_sizes": [],
    "dump_size_and_indexed_offset_errors": [],
    "alignment_unavailable_side_records": 1024,
    "derived_first_last_side_checks": 2048
  },
  "selected-checker": {
    "size_checks": 70,
    "changed_sizes": 54,
    "missing_sizes": [],
    "unexpected_sizes": [],
    "wrong_sizes": [],
    "duplicate_sizes": [],
    "dump_size_and_indexed_offset_errors": [],
    "alignment_unavailable_side_records": 70,
    "derived_first_last_side_checks": 140
  }
}
```
