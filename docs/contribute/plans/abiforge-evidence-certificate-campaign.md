# Evidence completion and executable reduction certificate

This follow-up uses the same340 flat ordinary record shapes and four packing caps
as the [configuration campaign](abiforge-configuration-campaign.md). No additional
language completeness claim is made.

## Alignment evidence lane

Matching headers use explicit pragma-pack push/pop for each cap. Debug information
is stripped from copies of the original DSOs, then public dump uses CastXML with
those headers. The GCC probes remain the independent layout oracle. All1360
alignment facts were available and exact, compared with1360 unknowns in the
previous DWARF-only lane. This result depends on matching headers; mismatched or
missing build context is not covered. CastXML was installed at
`/tmp/cutoff-castxml/castxml/bin`; adjust the runner PATH for another installation.

Sixteen comparisons were audited against5440 alignment obligations:3696 changed,
1744 unchanged, zero missing/extra/wrong-value findings. All884 alignment-only
pairs were included. This closes the measured acquisition gap in this header lane,
not in arbitrary DWARF files. No production detector fix was needed.

Reproduce after the earlier packing campaign, saving each code block under
`research/alignment-campaign/` with the indicated filename.

### run.py

```python
from pathlib import Path
import subprocess as sp,json,sys,os,re
P=Path(__file__).resolve().parent;ROOT=P.parents[1];B=P.parent/'packing-campaign'
env=os.environ|{'PATH':'/tmp/cutoff-castxml/castxml/bin:'+os.environ['PATH']}
commands=[]
def run(a):
 r=sp.run(list(map(str,a)),cwd=ROOT,env=env,capture_output=True,text=True,timeout=180);commands.append(dict(argv=list(map(str,a)),rc=r.returncode,stderr=r.stderr));assert r.returncode==0,r.stderr;return r.stdout
structs='\n'.join(x for x in (B/'probe.cpp').read_text().splitlines() if x.startswith('struct T'))
obs=json.loads((B/'results.json').read_text());expected={}
for cap in [1,2,4,8]:
 values=[x['layout'] for x in obs if x['opt']=='O0' and x['cap']==cap];expected[cap]=values
 header=P/f'pack{cap}.hpp';header.write_text(f'#pragma pack(push,{cap})\n'+structs+'\n#pragma pack(pop)\n'+'\n'.join(f'extern "C" unsigned long long probe{i}(const T{i}*);' for i in range(340)))
 lib=P/f'pack{cap}.so';run(['objcopy','--strip-debug',B/f'pack{cap}.so',lib])
 run([sys.executable,'-m','abicheck','dump',lib,'-H',header,'-o',P/f'pack{cap}.json'])
errors=[];unknown=0;checked=0
for cap in [1,2,4,8]:
 d=json.loads((P/f'pack{cap}.json').read_text());ts={t['name']:t for t in d['sections']['types']['payload']['types']}
 for i,w in enumerate(expected[cap]):
  actual=ts.get(f'T{i}',{}).get('alignment_bits');checked+=1
  if actual is None:unknown+=1
  elif actual!=w[1]*8:errors.append([cap,i,w[1]*8,actual])
summary=dict(alignment_obligations=checked,unknown=unknown,wrong=errors)
(P/'summary.json').write_text(json.dumps(summary,indent=2));(P/'commands.json').write_text(json.dumps(commands,indent=2));print(summary)

```

### compare.py

```python
from pathlib import Path
import json,subprocess as sp,sys,itertools
P=Path(__file__).resolve().parent;ROOT=P.parents[1];rows=[]
for a,b in itertools.product([1,2,4,8],repeat=2):
 out=P/f'compare-{a}-{b}.json'
 r=sp.run([sys.executable,'-m','abicheck','compare',str(P/f'pack{a}.json'),str(P/f'pack{b}.json'),'-o','json='+str(out)],cwd=ROOT,capture_output=True,text=True,timeout=120)
 assert r.returncode in [0,1,2,4],r.stderr
 d=json.loads(out.read_text());rows.append(dict(old=a,new=b,verdict=d['verdict'],kinds=sorted({c['kind'] for c in d['changes']})))
(P/'compare-summary.json').write_text(json.dumps(rows,indent=2));print(rows)

```

### audit.py

```python
import json,itertools
from pathlib import Path
P=Path(__file__).resolve().parent
obs=json.loads((P.parent/'packing-campaign/results.json').read_text());v={c:[r['layout'] for r in obs if r['opt']=='O0' and r['cap']==c] for c in [1,2,4,8]}
errors=[];changed=0;only=0
for a,b in itertools.product(v,repeat=2):
 d=json.loads((P/f'compare-{a}-{b}.json').read_text());got={c['symbol']:(int(c['old_value']),int(c['new_value'])) for c in d['changes'] if c['kind']=='type_alignment_changed'}
 for i,(x,y) in enumerate(zip(v[a],v[b])):
  want=(x[1]*8,y[1]*8) if x[1]!=y[1] else None
  changed+=want is not None
  only+=want is not None and x[0]==y[0] and x[2:]==y[2:]
  if got.get(f'T{i}')!=want:errors.append([a,b,i,want,got.get(f'T{i}')])
r=dict(obligations=5440,changed=changed,alignment_only=only,errors=errors)
(P/'audit.json').write_text(json.dumps(r,indent=2));print(r)

```

## Reduction certificate

# Packing reduction certificate

The certificate exhaustively verifies **5,440 directed type/cap cases** via
**4,438 canonical representatives** (1,002 fewer cases, or 18.42%). The
domain includes identity pairs and both directions of each unequal cap
pair. Among the dense cases, 884 change alignment alone.

| Types | Cap pairs per type | Probe observations checked | Types with 1 / 2 / 3 / 4 layout classes |
|---:|---:|---:|---:|
| 340 | 16 | 2,720 | 4 / 26 / 90 / 220 |

## Run from the repository root

```sh
python research/reduction-cert/certificate.py build
python research/reduction-cert/certificate.py verify
python -B -m unittest discover -s research/reduction-cert -v
```

Both commands print the summary. `build` writes `certificate.json`.
Use `--results` and `--certificate` to supply alternate paths.
Verification returns exit 1 on invalid input or a failed check.

## Scope and evidence

The certificate binds the byte-level SHA-256 of
`../packing-campaign/results.json`, the capped natural alignment model
version, the full projection, and compiler metadata reported by the
campaign (GCC 14.2, Linux x86-64, C++20, O0/O2, packing caps 1/2/4/8).
The verifier checks every expected observation against the model and
rejects missing or duplicate observations. The model lays out flat records
of one to four ordered unsigned scalar fields with natural size and
alignment in {1, 2, 4, 8} bytes, capping each field's alignment and
rounding final size to the record's maximum capped field alignment.

| Evidence lane | Role and scope |
|---|---|
| GCC runtime probes | Certificate input. The probes report `sizeof`, `alignof`, and every `offsetof`. O0 supplies the mapping and both O0 and O2 are checked against the model. |
| DWARF on the four original O0 DSOs | Separate checker audit. Size and field-offset findings were audited. Alignment was unknown for all 1,360 type records. |
| CastXML with matching `#pragma pack` headers on stripped copies of the same four DSOs | Separate follow-up reported by the parent campaign. All 1,360 alignment obligations were known and exactly matched independent GCC probes. The certificate consumes and hashes the GCC observations only. |

## Domain, verification, and claim

`T<index>:<old cap>-><new cap>` uses the campaign's zero-based type
index, generated by field count and then lexicographic order of field
sizes. Two cases share a class exactly when their type and both ordered
layouts match, including size, alignment, and every field offset. The
canonical representative is the smallest numeric (old cap, new cap) in
that class. The verifier reconstructs the domain and checks source and
target membership, total coverage, full equivalence, representative
self-mapping, canonical selection, and the reported counts.

The conditional argument is that any predicate factoring entirely through
(type, old full layout, new full layout) takes the same value on each
case and its representative, because its inputs are equal. The finite
check establishes the required equality for this campaign. The
certificate's universal ABI theorem status is **not established**. Its
scope excludes call protocols, nested records, bases, bitfields, unions,
arrays, vectors, explicit alignment, nontrivial objects, and other
compilers and targets.

Dropping alignment would merge `T1:1->2` into `T1:1->1` despite
the former changing alignment from 1 to 2 bytes. The tests reject both
an explicitly weakened projection and a mapping built with that
projection while declaring the full tuple.

### certificate.py

Save under `research/reduction-cert/`.

```python
#!/usr/bin/env python3
"""Build or verify the finite packing-campaign reduction certificate."""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
from itertools import product
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "packing-campaign" / "results.json"
CERTIFICATE = HERE / "certificate.json"
CAPS = (1, 2, 4, 8)
OPTS = ("O0", "O2")
TYPES = tuple(fields for n in range(1, 5) for fields in product(CAPS, repeat=n))
CAP_PAIRS = tuple(product(CAPS, repeat=2))
PROJECTION = ("size", "alignment", "all_field_offsets")
STATUS = {
    "finite": "exhaustive_for_declared_domain",
    "theorem": "conditional_factorization_only",
    "universal_abi_theorem": "not_established",
}


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def round_up(value: int, alignment: int) -> int:
    return ((value + alignment - 1) // alignment) * alignment


def model_layout(fields: tuple[int, ...], cap: int) -> tuple[int, ...]:
    alignments = tuple(min(size, cap) for size in fields)
    alignment = max(alignments)
    end = 0
    offsets = []
    for size, field_alignment in zip(fields, alignments):
        offset = round_up(end, field_alignment)
        offsets.append(offset)
        end = offset + size
    return (round_up(end, alignment), alignment, *offsets)


def read_campaign(path: Path) -> tuple[dict, dict]:
    raw = path.read_bytes()
    rows = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(rows, list):
        raise ValueError("observations must be a list")
    expected = set(product(OPTS, CAPS, TYPES))
    observations = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"opt", "cap", "fields", "layout"}:
            raise ValueError(f"invalid observation row {index}")
        opt, cap, fields, layout = (row[key] for key in ("opt", "cap", "fields", "layout"))
        if (
            not isinstance(opt, str)
            or type(cap) is not int
            or not isinstance(fields, list)
            or any(type(value) is not int for value in fields)
            or not isinstance(layout, list)
            or any(type(value) is not int for value in layout)
        ):
            raise ValueError(f"invalid observation values in row {index}")
        key = (opt, cap, tuple(fields))
        if key not in expected or key in observations:
            raise ValueError(f"unexpected or duplicate observation: {key}")
        if tuple(layout) != model_layout(key[2], cap):
            raise ValueError(f"layout model mismatch: {key}")
        observations[key] = tuple(layout)
    if missing := expected - observations.keys():
        raise ValueError(f"observation coverage: missing {len(missing)} rows, first {min(missing)}")

    scope = {
        "model": "flat-unsigned-scalars-capped-natural-alignment-v1",
        "compiler": {
            "reported_by": "packing-campaign/REPORT.md",
            "identity": "GCC 14.2",
            "target": "Linux x86-64",
            "flags": ["-std=c++20", "-O0/-O2", "-fpack-struct=1/2/4/8"],
        },
        "observations_sha256": sha256(raw).hexdigest(),
        "producer_evidence_lane": "GCC sizeof, alignof, and offsetof runtime probes",
        "optimization_levels_checked": list(OPTS),
        "layout_source": "O0, with O2 independently checked against the same model",
        "caps": list(CAPS),
        "field_sizes_and_natural_alignments_bytes": list(CAPS),
        "ordered_field_count_range": [1, 4],
        "projection_bytes": list(PROJECTION),
    }
    return scope, observations


def domain(observations: dict) -> dict:
    """Use campaign T indices and include both directions and identities."""
    cases = {}
    for index, fields in enumerate(TYPES):
        for old, new in CAP_PAIRS:
            cases[f"T{index}:{old}->{new}"] = (
                index, observations["O0", old, fields], observations["O0", new, fields]
            )
    return cases


def representatives(cases: dict) -> dict:
    """The first member has the smallest numeric (old cap, new cap)."""
    first = {}
    return {case: first.setdefault(layout_pair, case) for case, layout_pair in cases.items()}


def summarize(cases: dict, mapping: dict, observations: dict) -> dict:
    histogram = Counter(
        len({observations["O0", cap, fields] for cap in CAPS}) for fields in TYPES
    )
    return {
        "types": len(TYPES),
        "observations_checked": len(observations),
        "domain_cases": len(cases),
        "representatives": len(set(mapping.values())),
        "alignment_only_cases": sum(
            old[0] == new[0] and old[2:] == new[2:] and old[1] != new[1]
            for _, old, new in cases.values()
        ),
        "layout_class_histogram": {str(k): v for k, v in sorted(histogram.items())},
    }


def build(scope: dict, observations: dict) -> dict:
    cases = domain(observations)
    mapping = representatives(cases)
    return {
        "schema_version": 1,
        "scope": scope,
        "status": STATUS,
        "summary": summarize(cases, mapping, observations),
        "mapping": mapping,
    }


def validate(certificate: dict, scope: dict, observations: dict) -> None:
    if not isinstance(certificate, dict) or certificate.get("schema_version") != 1:
        raise ValueError("unsupported certificate schema")
    declared_scope = certificate.get("scope")
    if not isinstance(declared_scope, dict):
        raise ValueError("missing certificate scope")
    if declared_scope.get("observations_sha256") != scope["observations_sha256"]:
        raise ValueError("observation SHA-256 digest mismatch")
    if declared_scope.get("projection_bytes") != list(PROJECTION):
        raise ValueError("projection must include size, alignment, and all field offsets")
    if declared_scope != scope or certificate.get("status") != STATUS:
        raise ValueError("certificate scope or proof status mismatch")

    cases = domain(observations)
    mapping = certificate.get("mapping")
    if not isinstance(mapping, dict):
        raise ValueError("missing case mapping")
    if extra := mapping.keys() - cases.keys():
        raise ValueError(f"domain membership: unexpected case {min(extra)}")
    if missing := cases.keys() - mapping.keys():
        raise ValueError(f"domain coverage: missing case {min(missing)}")

    canonical = representatives(cases)
    for case, representative in mapping.items():
        if not isinstance(representative, str) or representative not in cases:
            raise ValueError(f"representative membership failed for {case}: {representative!r}")
        if cases[case] != cases[representative]:
            raise ValueError(f"full layout equivalence failed: {case} -> {representative}")
        if mapping[representative] != representative:
            raise ValueError(f"representative must self-map: {representative}")
        if canonical[case] != representative:
            raise ValueError(f"noncanonical representative for {case}: {representative}")
    if certificate.get("summary") != summarize(cases, mapping, observations):
        raise ValueError("certificate summary mismatch")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "verify"))
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--certificate", type=Path, default=CERTIFICATE)
    args = parser.parse_args()
    try:
        scope, observations = read_campaign(args.results)
        if args.command == "build":
            certificate = build(scope, observations)
            validate(certificate, scope, observations)
            args.certificate.write_text(json.dumps(certificate, indent=2) + "\n")
        else:
            certificate = json.loads(
                args.certificate.read_bytes(), object_pairs_hook=unique_object
            )
            validate(certificate, scope, observations)
    except (OSError, ValueError) as error:
        parser.exit(1, f"certificate error: {error}\n")
    print(json.dumps(certificate["summary"], indent=2))


if __name__ == "__main__":
    main()

```

### test_certificate.py

Save under `research/reduction-cert/`.

```python
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import certificate as cert


class CertificateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scope, cls.observations = cert.read_campaign(cert.RESULTS)
        cls.baseline = cert.build(cls.scope, cls.observations)

    def setUp(self) -> None:
        self.certificate = deepcopy(self.baseline)

    def verify(self) -> None:
        cert.validate(self.certificate, self.scope, self.observations)

    def test_checked_in_certificate(self) -> None:
        self.certificate = json.loads(cert.CERTIFICATE.read_bytes())
        self.verify()
        self.assertEqual(self.certificate, self.baseline)
        summary = self.certificate["summary"]
        self.assertEqual(summary["domain_cases"], 5440)
        self.assertEqual(summary["representatives"], 4438)
        self.assertEqual(summary["alignment_only_cases"], 884)

    def test_tampered_mapping(self) -> None:
        self.certificate["mapping"]["T0:1->1"] = "T1:1->1"
        with self.assertRaisesRegex(ValueError, "full layout equivalence"):
            self.verify()

    def test_removed_case(self) -> None:
        del self.certificate["mapping"]["T0:1->1"]
        with self.assertRaisesRegex(ValueError, "domain coverage"):
            self.verify()

    def test_extra_case(self) -> None:
        self.certificate["mapping"]["T340:1->1"] = "T0:1->1"
        with self.assertRaisesRegex(ValueError, "domain membership"):
            self.verify()

    def test_representative_outside_domain(self) -> None:
        self.certificate["mapping"]["T0:1->1"] = "T0:1->3"
        with self.assertRaisesRegex(ValueError, "representative membership"):
            self.verify()

    def test_representative_must_self_map(self) -> None:
        self.certificate["mapping"]["T0:1->1"] = "T0:1->2"
        with self.assertRaisesRegex(ValueError, "must self-map"):
            self.verify()

    def test_equivalent_self_mapping_representative_must_be_canonical(self) -> None:
        self.certificate["mapping"]["T0:1->2"] = "T0:1->2"
        with self.assertRaisesRegex(ValueError, "noncanonical representative"):
            self.verify()

    def test_changed_digest(self) -> None:
        self.certificate["scope"]["observations_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256 digest mismatch"):
            self.verify()

    def test_removed_observation(self) -> None:
        rows = json.loads(cert.RESULTS.read_bytes())
        with TemporaryDirectory() as temp:
            path = Path(temp) / "results.json"
            path.write_text(json.dumps(rows[:-1]))
            with self.assertRaisesRegex(ValueError, "observation coverage"):
                cert.read_campaign(path)

    def test_omitted_alignment_projection(self) -> None:
        self.certificate["scope"]["projection_bytes"].remove("alignment")
        with self.assertRaisesRegex(ValueError, "projection must include"):
            self.verify()

    def test_projected_mapping_loses_alignment_only_change(self) -> None:
        cases = cert.domain(self.observations)
        # T1 is a single 2-byte scalar. The cap change affects alignment alone.
        self.assertEqual(cases["T1:1->2"], (1, (2, 1, 0), (2, 2, 0)))
        self.assertEqual(cases["T1:1->1"], (1, (2, 1, 0), (2, 1, 0)))
        projected = {
            case: (index, old[:1] + old[2:], new[:1] + new[2:])
            for case, (index, old, new) in cases.items()
        }
        self.certificate["mapping"] = cert.representatives(projected)
        self.assertEqual(self.certificate["mapping"]["T1:1->2"], "T1:1->1")
        with self.assertRaisesRegex(ValueError, r"full layout equivalence failed: T1:1->2"):
            self.verify()


if __name__ == "__main__":
    unittest.main()

```
