# Finding projection mutation audit

This targets the main Finding/EntityClaim projection only, not all oracles.


## run.py

```python
from pathlib import Path
import json,subprocess,importlib.util,sys,copy
P=Path(__file__).resolve().parent
rev='691862f6740c5e5bfc66326e29925dff56de3d91'
source=subprocess.check_output(['git','show',rev+':abiforge/model/finding.py'],text=True)
(P/'finding_source.py').write_text(source)
spec=importlib.util.spec_from_file_location('campaign_finding',P/'finding_source.py');m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
r=json.loads((P.parent/'alignment-campaign/compare-1-8.json').read_text())
raw=next(x for x in r['changes'] if x['kind']=='type_field_offset_changed')
def project(x):return m.Finding(kind=x['kind'],symbol=x['symbol'],impact='BREAKING')
claim=m.EntityClaim(entity=m.EntityRef(raw['symbol']),required=True,kinds=frozenset([raw['kind']]))
rows=[]
for label,updates in [('baseline',{}),('wrong_field',{'field_name':'invented_member'}),('wrong_old_value',{'old_value':'987654'}),('wrong_new_value',{'new_value':'987654'}),('wrong_entity',{'symbol':'DifferentRecord'})]:
 x=copy.deepcopy(raw);x.update(updates)
 rows.append(dict(case=label,claim_violation=claim.violation((project(x),),{}),same_projection=project(x)==project(raw),raw_changed=x!=raw))
(P/'results.json').write_text(json.dumps(dict(revision=rev,baseline={k:raw.get(k) for k in ['kind','symbol','field_name','old_value','new_value']},cases=rows),indent=2));print(rows)

```

## results.json

```json
{
  "revision": "691862f6740c5e5bfc66326e29925dff56de3d91",
  "baseline": {
    "kind": "type_field_offset_changed",
    "symbol": "T5",
    "field_name": "f1",
    "old_value": "8",
    "new_value": "16"
  },
  "cases": [
    {
      "case": "baseline",
      "claim_violation": null,
      "same_projection": true,
      "raw_changed": false
    },
    {
      "case": "wrong_field",
      "claim_violation": null,
      "same_projection": true,
      "raw_changed": true
    },
    {
      "case": "wrong_old_value",
      "claim_violation": null,
      "same_projection": true,
      "raw_changed": true
    },
    {
      "case": "wrong_new_value",
      "claim_violation": null,
      "same_projection": true,
      "raw_changed": true
    },
    {
      "case": "wrong_entity",
      "claim_violation": "missing claim on T5: none of ['type_field_offset_changed'] attributed to it",
      "same_projection": false,
      "raw_changed": true
    }
  ]
}
```
