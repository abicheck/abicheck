# Adversarial validation campaign

The counts below are mutation and orchestration iterations, not new compiled ABI
scenarios. Original compiler evidence remains pinned to the preceding campaigns.

## exact-finding-campaign

# Exact finding mutation campaign

240 deterministic scoring iterations reuse16 real header-backed reports from the
alignment campaign. These are report mutations, not240 new compiler experiments.
Independent GCC probes generate required sizes, alignments and member offsets for
340 types and16 directed cap pairs. Expected values are converted to bits before
matching the public report representation. The matcher compares a multiset keyed
by kind, type, field when applicable, old value and new value.

32 controls (original and reordered reports) pass. All208 mutated reports fail as
expected: wrong entity, old value, new value, field identity, removed finding,
duplicated finding and invented extra finding. Duplicate sensitivity is deliberate:
a set would conceal repeated findings. No unexpected outcome occurred.

This is a prototype supplemental audit for three kinds: type_size_changed,
type_alignment_changed and type_field_offset_changed. Other kinds are deliberately
outside the projection; their absence/presence is not scored. Reports do not need to
match ordering or prose. Semantic impact, reachability and policy are not certified
by exact numeric equality. Missing required input observations must be classified
before invoking this scorer; the campaign uses fully available GCC probes.

Run python research/exact-finding-campaign/run.py after the packing and alignment
campaigns. The verifier assertions are anchored to independent probe data, not the
checker baseline. This closes the specific member/payload projection limitation
observed in the earlier Finding audit without replacing abiforge EntityClaim.

Integration: add a separate typed payload obligation to campaign adapters; retain
kind/entity claims, explicit units and multiset cardinality. Acceptance includes
this mutation corpus and no change to verdict policy. Extend via reviewed per-kind
schemas rather than applying one ad-hoc JSON equality rule to all finding kinds.

A deliberately coarse baseline comparing only the set of (kind, symbol) accepts
120 of208 mutants. This baseline illustrates lost information; it is not an
end-to-end measurement of all current abiforge checks. Exact multiset scoring
rejects all208 under this independent numeric oracle.

Save as `research/exact-finding-campaign/run.py`.

```python
from pathlib import Path
from collections import Counter
import json,itertools,copy
P=Path(__file__).resolve().parent;R=P.parent
K={'type_size_changed','type_alignment_changed','type_field_offset_changed'}
def key(c):return (c['kind'],c['symbol'],c.get('field_name') if c['kind']=='type_field_offset_changed' else None,str(c['old_value']),str(c['new_value']))
def score(report,expected):
 got=Counter(key(c) for c in report if c['kind'] in K)
 return dict(missing=list((expected-got).elements()),unexpected=list((got-expected).elements()))
def passed(result):return not result['missing'] and not result['unexpected']
obs=json.loads((R/'packing-campaign/results.json').read_text());v={c:[r['layout'] for r in obs if r['opt']=='O0' and r['cap']==c] for c in [1,2,4,8]};rows=[]
for a,b in itertools.product(v,repeat=2):
 expected=Counter()
 for i,(x,y) in enumerate(zip(v[a],v[b])):
  for kind,j in [('type_size_changed',0),('type_alignment_changed',1)]:
   if x[j]!=y[j]:expected[(kind,f'T{i}',None,str(x[j]*8),str(y[j]*8))]+=1
  for j,(u,w) in enumerate(zip(x[2:],y[2:])):
   if u!=w:expected[('type_field_offset_changed',f'T{i}',f'f{j}',str(u*8),str(w*8))]+=1
 raw=json.loads((R/'alignment-campaign'/f'compare-{a}-{b}.json').read_text())['changes']
 def case(name,data,want):
  s=score(data,expected);out=passed(s);weak={(c['kind'],c['symbol']) for c in data if c['kind'] in K}=={(k[0],k[1]) for k in expected};rows.append(dict(old=a,new=b,case=name,expected_accept=want,accepted=out,kind_entity_set_accept=weak,matched=out==want,missing=len(s['missing']),unexpected=len(s['unexpected'])))
 case('valid',raw,True);case('reordered',list(reversed(raw)),True)
 for kind in sorted(K):
  indexes=[i for i,c in enumerate(raw) if c['kind']==kind]
  if not indexes:continue
  i=indexes[0]
  for name,field,value in [('wrong_entity','symbol','InventedType'),('wrong_old','old_value','987654'),('wrong_new','new_value','987654')]+([('wrong_member','field_name','invented')] if kind=='type_field_offset_changed' else []):
   data=copy.deepcopy(raw);data[i][field]=value;case(kind+'/'+name,data,False)
  data=copy.deepcopy(raw);data.pop(i);case(kind+'/missing',data,False)
  data=copy.deepcopy(raw);data.append(copy.deepcopy(data[i]));case(kind+'/duplicate',data,False)
 fake=dict(kind='type_size_changed',symbol='InventedType',old_value='8',new_value='16')
 case('extra',raw+[fake],False)
summary=dict(iterations=len(rows),controls=sum(r['expected_accept'] for r in rows),mutations=sum(not r['expected_accept'] for r in rows),unexpected_outcomes=sum(not r['matched'] for r in rows),mutants_accepted_by_kind_entity_set=sum(not r['expected_accept'] and r['kind_entity_set_accept'] for r in rows),scope='exact multiset of3 kinds; independent GCC layouts; no semantic verdict completeness')
(P/'results.json').write_text(json.dumps(dict(summary=summary,iterations=rows),indent=2));print(summary)
assert not summary['unexpected_outcomes']

```

## certificate-mutation-campaign

# Certificate mutation campaign

The checked-in flat and nested certificates both passed baseline verification.
Across **59 distinct mutated input pairs**, the verifiers rejected all **51**
expected-reject cases at the expected stage with the expected diagnostic and
accepted all **8** harmless controls. The two baselines are excluded from these
counts. The campaign found no unexpected behavior in the cases exercised.

| Family | Flat rejects | Nested rejects | Flat controls accepted | Nested controls accepted |
|---|---:|---:|---:|---:|
| Mapping coverage | 4 | 4 | 0 | 0 |
| Representative validity | 3 | 3 | 0 | 0 |
| Representative canonicality | 1 | 1 | 0 | 0 |
| Representative self-map | 1 | 1 | 0 | 0 |
| Projection omission | 3 | 3 | 0 | 0 |
| Hash binding | 3 | 3 | 0 | 0 |
| Domain metadata | 5 | 4 | 0 | 0 |
| Observations tamper | 6 | 6 | 0 | 0 |
| Harmless controls | 0 | 0 | 4 | 4 |
| **Total** | **26** | **25** | **4** | **4** |

## Cases and checks

- **Mapping coverage.** Delete an alias, delete a canonical representative, add an out-of-domain key, or repeat a key inside the raw mapping object. Both JSON decoders rejected the repeated key before verification.
- **Representative validity.** Assign an unknown target, a numeric target, or an in-domain representative with different recorded layout observations.
- **Representative canonicality.** Replace every assignment in one equivalence class with the same equivalent alternate leader. The alternate self-maps and observations remain equivalent, so both verifiers reached the canonicality check.
- **Representative self-map.** Redirect the canonical leader to an alias that still points back to the leader. The flat verifier reported a self-map violation. The nested verifier reported a canonicality violation.
- **Projection omission.** Remove size, alignment, or all field offsets in the flat certificate. Remove enum size, enum alignment, or array stride in the nested certificate.
- **Hash binding.** Replace the digest, delete it, or append whitespace to the observation input while retaining the original digest.
- **Domain metadata.** Both targets reject an unsupported schema version and a false domain count. The flat target also rejects a missing declared cap, a narrowed field-count range, and altered proof status. The nested target also rejects an altered declared campaign path and target.
- **Observations tamper.** Both readers reject a deleted, repeated, or out-of-domain row. The flat reader also rejects isolated changes to size, alignment, and first offset against its layout model. The nested reader rejects changing observed enum size alone, omitting observed stride, or marking a probe unmatched. These checks exercise the nested reader's recorded expectations, projection shape, and status.
- **Harmless controls.** Both targets accept added certificate whitespace, reversed mapping order, added observation whitespace with a refreshed digest, and reversed observation row order with a refreshed digest. The refreshed-digest controls update only the digest in the certificate's data.

## Reproduce

From the repository root, run:

```sh
python -B research/certificate-mutation-campaign/campaign.py
```

The script loads each sibling verifier by its explicit file path under a unique
module name. It calls the observation reader, decodes certificate bytes with
the verifier's duplicate-key hook, and calls its verification function. This
uses the CLI's verification sequence. It keeps observation inputs in a
temporary directory and overwrites `results.json` with the
baseline, case outcomes, expected and actual stages, diagnostics, observed
signatures, and source and input SHA-256 fingerprints. An expectation mismatch
produces a nonzero exit status.

| Verifier | Signatures captured with `inspect.signature` |
|---|---|
| Flat | `read_campaign(path: 'Path') -> 'tuple[dict, dict]'`, `unique_object(pairs: 'list[tuple[str, object]]') -> 'dict'`, `validate(certificate: 'dict', scope: 'dict', observations: 'dict') -> 'None'` |
| Nested | `read_campaign(path)`, `unique_object(pairs)`, `verify(certificate, provenance, observations)` |

The results characterize these fixtures and mutations. The campaign uses the
checked-in observations and does not rerun compiler probes.

Save as `research/certificate-mutation-campaign/campaign.py`.

```python
#!/usr/bin/env python3
"""Exercise the two checked-in finite-layout verifiers on deterministic inputs."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from inspect import signature
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType

HERE = Path(__file__).resolve().parent


def load_verifier(name: str) -> ModuleType:
    path = HERE.parent / name / "certificate.py"
    spec = spec_from_file_location(f"campaign_{name.replace('-', '_')}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {path}")
    module = module_from_spec(spec)
    # The sibling modules guard their CLI entry points with __name__.
    # Suppress import bytecode writes in those directories.
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def encode(value: object) -> bytes:
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


@dataclass
class Mutation:
    family: str
    name: str
    certificate: bytes
    observations: bytes
    rejection: tuple[str, str] | None


def mutations(cert_raw: bytes, obs_raw: bytes, nested: bool, other_layout: str) -> list[Mutation]:
    cert = json.loads(cert_raw)
    obs = json.loads(obs_raw)
    mapping = cert["mapping"]
    first, alternate = next((rep, case) for case, rep in mapping.items() if case != rep)
    alias_class = [case for case, rep in mapping.items() if rep == first]
    rows = obs["probes"] if nested else obs
    scope = ("provenance",) if nested else ("scope",)
    digest = (*scope, "results_sha256" if nested else "observations_sha256")
    projection = ("projection_bytes",) if nested else ("scope", "projection_bytes")
    missing = object()
    cases = []

    def add(family: str, name: str, rejection: tuple[str, str] | None,
            certificate: bytes = cert_raw, observations: bytes = obs_raw) -> None:
        cases.append(Mutation(family, name, certificate, observations, rejection))

    def change(
        family: str, name: str, path: tuple, value: object, error: str,
        *, observations: bool = False,
    ) -> None:
        document = deepcopy(obs if observations else cert)
        parent = document
        for key in path[:-1]:
            parent = parent[key]
        if value is missing:
            del parent[path[-1]]
        else:
            parent[path[-1]] = value
        stage = "read_campaign" if observations else "verify_certificate"
        payload = {"observations" if observations else "certificate": encode(document)}
        add(family, name, (stage, error), **payload)

    coverage = "mapping coverage" if nested else "domain coverage"
    membership = "mapping coverage" if nested else "domain membership"
    change("mapping coverage", "delete-alias", ("mapping", alternate), missing, coverage)
    change("mapping coverage", "delete-representative", ("mapping", first), missing, coverage)
    change("mapping coverage", "add-outside-case", ("mapping", "outside-domain"), first, membership)
    # Insert a repeated member into mapping JSON before decoding can collapse it.
    member = encode(first).strip() + b":" + encode(first).strip()
    mapping_prefix = b'"mapping":{'
    prefix, mapping_json = encode(cert).split(mapping_prefix, 1)
    duplicate = prefix + mapping_prefix + mapping_json.replace(member, member + b"," + member, 1)
    add("mapping coverage", "duplicate-json-case", ("decode_certificate", "duplicate JSON key"),
        certificate=duplicate)

    invalid = "invalid representative" if nested else "representative membership"
    change("representative validity", "unknown-target", ("mapping", alternate), "outside-domain", invalid)
    change("representative validity", "numeric-target", ("mapping", alternate), 0, invalid)
    layout_error = "full layout mismatch" if nested else "full layout equivalence"
    change("representative validity", "different-layout-target", ("mapping", alternate), other_layout, layout_error)
    changed_class = mapping | dict.fromkeys(alias_class, alternate)
    change("representative canonicality", "substitute-equivalent-class-leader",
           ("mapping",), changed_class, "noncanonical representative")
    selfmap_error = "noncanonical representative" if nested else "representative must self-map"
    change("representative self-map", "redirect-leader-to-alias",
           ("mapping", first), alternate, selfmap_error)

    labels = cert["projection_bytes"] if nested else cert["scope"]["projection_bytes"]
    for label in (labels[0], labels[1], labels[-1]):
        change("projection omission", f"omit-{label}", projection,
               [entry for entry in labels if entry != label], "projection must")

    hash_error = "campaign SHA-256" if nested else "observation SHA-256"
    change("hash binding", "replace-digest", digest, "0" * 64, hash_error)
    change("hash binding", "delete-digest", digest, missing, hash_error)
    add("hash binding", "whitespace-with-stale-digest", ("verify_certificate", hash_error),
        observations=obs_raw + b"\n")

    change("domain metadata", "unsupported-schema", ("schema_version",), 2, "unsupported certificate schema")
    if nested:
        change("domain metadata", "replace-campaign-path", (*scope, "campaign"), "other.json", "campaign provenance mismatch")
        change("domain metadata", "replace-declared-target", (*scope, "environment", "target"),
               "other-target", "campaign provenance mismatch")
    else:
        change("domain metadata", "drop-declared-cap", (*scope, "caps"), [1, 2, 4], "scope or proof status mismatch")
        change("domain metadata", "narrow-field-count", (*scope, "ordered_field_count_range"),
               [1, 3], "scope or proof status mismatch")
        change("domain metadata", "replace-proof-status", ("status", "finite"), "sampled", "scope or proof status mismatch")
    count_key = "configurations" if nested else "domain_cases"
    change("domain metadata", "replace-domain-count", ("summary", count_key), 0, "summary mismatch")

    row_path = ("probes",) if nested else ()
    unexpected = "unexpected or duplicate probe" if nested else "unexpected or duplicate observation"
    coverage_error = "probe coverage" if nested else "observation coverage"
    for name, changed_rows, error in (
        ("delete-observation", rows[1:], coverage_error),
        ("duplicate-observation", [*rows, rows[0]], unexpected),
    ):
        changed_obs = dict(obs, probes=changed_rows) if nested else changed_rows
        add("observations tamper", name, ("read_campaign", error), observations=encode(changed_obs))
    change("observations tamper", "outside-domain-observation",
           (*row_path, 0, "case" if nested else "opt"), "outside-domain", unexpected, observations=True)
    if nested:
        change("observations tamper", "replace-observed-size",
               (*row_path, 0, "observed", "E.size"), rows[0]["observed"]["E.size"] + 1,
               "differs from recorded expectation", observations=True)
        change("observations tamper", "omit-observed-stride",
               (*row_path, 0, "observed", "RecordArray.items.stride"), missing,
               "invalid observed projection", observations=True)
        change("observations tamper", "mark-probe-unmatched",
               (*row_path, 0, "status"), "unmatched", "probe did not match", observations=True)
    else:
        for index, name in enumerate(("size", "alignment", "first-offset")):
            change("observations tamper", f"replace-observed-{name}",
                   (0, "layout", index), rows[0]["layout"][index] + 1,
                   "layout model mismatch", observations=True)

    add("harmless controls", "certificate-whitespace", None, certificate=cert_raw + b"\n")
    reordered = dict(cert, mapping=dict(reversed(list(mapping.items()))))
    add("harmless controls", "reverse-mapping-order", None, certificate=encode(reordered))
    reversed_rows = list(reversed(rows))
    reordered_obs = dict(obs, probes=reversed_rows) if nested else reversed_rows
    for name, payload in (
        ("observation-whitespace-with-refreshed-digest", obs_raw + b"\n"),
        ("reverse-observation-order-with-refreshed-digest", encode(reordered_obs)),
    ):
        rebound = deepcopy(cert)
        rebound[scope[0]][digest[-1]] = sha256(payload).hexdigest()
        add("harmless controls", name, None, certificate=encode(rebound), observations=payload)
    return cases


def execute(module: ModuleType, verifier_name: str, mutation: Mutation, path: Path) -> dict:
    stage = "read_campaign"
    outcome = "accepted"
    diagnostic = None
    try:
        path.write_bytes(mutation.observations)
        metadata, observations = module.read_campaign(path)
        stage = "decode_certificate"
        certificate = json.loads(mutation.certificate, object_pairs_hook=module.unique_object)
        stage = "verify_certificate"
        getattr(module, verifier_name)(certificate, metadata, observations)
    except ValueError as error:
        outcome = "rejected"
        diagnostic = str(error)
    except Exception as error:
        outcome = "error"
        diagnostic = f"{type(error).__name__}: {error}"

    expected = mutation.rejection
    matched = outcome == "accepted" if expected is None else (
        outcome == "rejected" and stage == expected[0] and expected[1] in diagnostic
    )
    return {
        "family": mutation.family,
        "case": mutation.name,
        "expected": "accept" if expected is None else "reject",
        "expected_stage": None if expected is None else expected[0],
        "expected_diagnostic_fragment": None if expected is None else expected[1],
        "outcome": outcome,
        "stage": stage,
        "diagnostic": diagnostic,
        "matched_expectation": matched,
        "certificate_sha256": sha256(mutation.certificate).hexdigest(),
        "observations_sha256": sha256(mutation.observations).hexdigest(),
    }


def run_target(name: str, nested: bool, scratch: Path) -> dict:
    module = load_verifier(name)
    verifier_name = "verify" if nested else "validate"
    cert_raw = module.CERTIFICATE.read_bytes()
    obs_raw = module.RESULTS.read_bytes()
    baseline = execute(module, verifier_name, Mutation("baseline", "original", cert_raw, obs_raw, None), scratch)
    if not baseline["matched_expectation"]:
        raise RuntimeError(f"{name} baseline failed: {baseline}")
    cert = json.loads(cert_raw)
    _, observations = module.read_campaign(module.RESULTS)
    # The flat domain also includes a type index. Require a difference in the
    # observation tuples themselves for the different-layout case.
    layouts = observations if nested else {
        case: (old, new) for case, (_, old, new) in module.domain(observations).items()
    }
    mapping = cert["mapping"]
    first = next(rep for case, rep in mapping.items() if case != rep)
    other_layout = next(
        case for case in mapping if mapping[case] == case and layouts[case] != layouts[first]
    )
    cases = mutations(cert_raw, obs_raw, nested, other_layout)
    payloads = {(case.certificate, case.observations) for case in cases}
    if len(payloads) != len(cases) or (cert_raw, obs_raw) in payloads:
        raise RuntimeError(f"{name} campaign includes duplicate or unchanged inputs")
    return {
        "target": name,
        "verifier_sha256": sha256(Path(module.__file__).read_bytes()).hexdigest(),
        "signatures": {
            key: str(signature(getattr(module, key)))
            for key in ("read_campaign", "unique_object", verifier_name)
        },
        "baseline": baseline,
        "cases": [execute(module, verifier_name, case, scratch) for case in cases],
    }


def main() -> int:
    with TemporaryDirectory(prefix="certificate-mutations-") as scratch:
        path = Path(scratch) / "observations.json"
        targets = [
            run_target("reduction-cert", False, path),
            run_target("nested-reduction-cert", True, path),
        ]
    rows = [row for target in targets for row in target["cases"]]
    if len(rows) < 32:
        raise RuntimeError("Campaign requires at least 32 distinct mutations")
    totals = dict(Counter(row["outcome"] for row in rows))
    mismatches = sum(not row["matched_expectation"] for row in rows)
    results = {"targets": targets, "mutations": len(rows), "outcomes": totals, "expectation_mismatches": mismatches}
    (HERE / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"{len(rows)} mutations, {totals}, {mismatches} expectation mismatches")
    return int(bool(mismatches))


if __name__ == "__main__":
    sys.exit(main())

```

## Runner stress and repair

# Runner stress campaign

The same 28 CLI variants ran before and after the fix, using temporary
workspaces and controlled environments limited to synthetic `CAMPAIGN_TEST_*`
values. The JSON reports record the planned configurations, actual mutations,
child launches, receipts, and independent checks.

| Run | Matched expectations | Counterexamples | Harness errors |
| --- | ---: | ---: | ---: |
| `Before` | 27 | 1 | 0 |
| `After` | 28 | 0 | 0 |

In `input-mutation-earlier-cell`, the first cell passed, and a later cell
changed its declared input. Before the fix, the initial run exited 0 with
`complete: true`. Audit and resume rejected the stale fingerprint. After the
fix, final manifest validation under the receipt lock makes the initial run
exit 2 without a completeness summary. Audit and resume still exit 2, and
the receipt retains both execution records. The new regression reproduced the
failure before the fix. All 12 runner tests passed afterward.

From the repository root, write a fresh result while preserving both reports.

```sh
python3 -B research/runner-stress-campaign/stress.py --output /tmp/runner-stress-results.json
```

Save as `research/runner-stress-campaign/stress.py`.

```python
#!/usr/bin/env python3
"""Bounded, stdlib-only CLI stress campaign for the sibling POSIX prototype."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
RUNNER = HERE.parent / "campaign-runner" / "campaign.py"
KEY = "CAMPAIGN_TEST_MODE"
EXTRA = "CAMPAIGN_TEST_EXTRA"
CALL_LIMIT = 5
CHILD = """
import json, os, signal, sys, time
from pathlib import Path

cell, action = sys.argv[1:]
source = Path("input.txt")
observed = {"cell": cell, "mode": os.getenv("CAMPAIGN_TEST_MODE"),
            "extra": os.getenv("CAMPAIGN_TEST_EXTRA"), "input": source.read_text()}
with Path("launches.jsonl").open("a") as log:
    print(json.dumps(observed), file=log)
print(json.dumps(observed), flush=True)
if action == "fail" and not Path("ready").exists():
    sys.exit(7)
if action == "wait" and not Path("ready").exists():
    time.sleep(4)
if action == "signal" and not Path("ready").exists():
    os.kill(os.getpid(), signal.SIGTERM)
if action == "mutate":
    source.write_text("after")
"""


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


class Trial:
    def __init__(self, root):
        self.root = root
        self.spec = root / "spec.json"
        self.manifest = root / "manifest.json"
        self.receipt = root / "receipt.json"
        self.source = root / "input.txt"
        self.source.write_text("before")
        (root / "child.py").write_text(CHILD)
        self.operations = []
        self.checks = []
        self.configuration = None

    def configure(self, cells):
        spec = {"cells": [
            {"id": name, "cwd": ".", "argv": [sys.executable, "-I", "-S", "child.py", name, action],
             "inputs": ["child.py", *inputs], "timeout_seconds": timeout,
             "env_allowlist": allowlist}
            for name, action, inputs, timeout, allowlist in cells
        ]}
        write(self.spec, spec)
        self.configuration = read(self.spec)

    def argv(self, command, flags):
        paths = [self.spec, self.manifest] if command == "plan" else [self.manifest, self.receipt]
        return [sys.executable, "-I", "-B", str(RUNNER), "--root", str(self.root),
                command, *map(str, paths), *flags]

    def capture(self, command, env, flags, code, stdout, stderr):
        self.operations.append({
            "command": command, "environment": env, "flags": list(flags), "exit": code,
            "summary": json.loads(stdout) if stdout.strip() else None, "stderr": stderr.strip(),
            "launches": self.launches(), "receipt": self.receipt_snapshot(),
        })
        return self.operations[-1]

    def call(self, command, env, *flags):
        result = subprocess.run(self.argv(command, flags), env=env, text=True, capture_output=True,
                                timeout=CALL_LIMIT)
        return self.capture(command, env, flags, result.returncode, result.stdout, result.stderr)

    def interrupt(self, env):
        with subprocess.Popen(self.argv("run", ()), env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            try:
                deadline = time.monotonic() + 3
                while len(self.launches()) < 2 and process.poll() is None:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("second child did not announce its launch")
                    time.sleep(0.01)
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                stdout, stderr = process.communicate(timeout=CALL_LIMIT)
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    try:
                        process.communicate(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
        return self.capture("run", env, ("SIGINT after second launch",),
                            process.returncode, stdout, stderr)

    def launches(self):
        log = self.root / "launches.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines() if line.endswith("}")]

    def receipt_snapshot(self):
        if not self.receipt.exists():
            return None
        raw = self.receipt.read_bytes()
        try:
            parsed = json.loads(raw)
        except (ValueError, UnicodeError):
            parsed = None
        return {"sha256": hashlib.sha256(raw).hexdigest(), "parsed": parsed,
                "invalid_json": raw.decode(errors="replace") if parsed is None else None}

    def check(self, name, expected, actual):
        self.checks.append({"check": name, "expected": expected, "actual": actual,
                            "matched": expected == actual})

    def result(self, observation, code, **summary):
        self.check(observation["command"] + " exit", code, observation["exit"])
        for name, expected in summary.items():
            self.check(observation["command"] + " " + name, expected,
                       (observation["summary"] or {}).get(name))

    def boot(self, env):
        plan = self.call("plan", env)
        self.result(plan, 0, planned=len(self.configuration["cells"]))
        if plan["exit"] != 0:
            raise RuntimeError("could not establish fixture plan")

    def reasons(self):
        return {
            result["id"]: [attempt["reason"] for attempt in result["attempts"]]
            for result in read(self.receipt)["results"]
        }

    def mutate(self, target, before, after):
        self.operations.append({"mutation": target, "before": before, "after": after})


def cell(name="probe", action="pass", inputs=("input.txt",), timeout=2, allowlist=(KEY,)):
    return name, action, inputs, timeout, allowlist


def environment_case(trial, before, after, allowlist):
    trial.configure([cell(allowlist=allowlist)])
    trial.boot(before)
    baseline = trial.call("run", before)
    trial.result(baseline, 0, complete=True, **{"pass": 1})
    trial.mutate("runner environment", before, after)
    drift = any(before.get(key) != after.get(key) for key in allowlist)
    resumed = trial.call("run", after)
    audited = trial.call("audit", after)
    if drift:
        trial.result(resumed, 2)
        trial.result(audited, 2)
        trial.check("run rejects binding drift", True, "fingerprint mismatch" in resumed["stderr"])
        trial.check("audit rejects binding drift", True, "fingerprint mismatch" in audited["stderr"])
    else:
        trial.result(resumed, 0, complete=True, **{"pass": 1})
        trial.result(audited, 0, complete=True)
    trial.check("independent launch observations",
                [{"cell": "probe", "mode": before.get(KEY) if KEY in allowlist else None,
                  "extra": None, "input": "before"}], trial.launches())
    trial.check("receipt preserved", baseline["receipt"], trial.receipt_snapshot())


def input_case(trial, change):
    trial.configure([cell()])
    trial.boot({})
    baseline = trial.call("run", {})
    trial.result(baseline, 0, complete=True)
    before = trial.source.stat()
    if change == "delete":
        trial.source.unlink()
        observed = {"exists": trial.source.exists()}
    elif change == "same-bytes":
        replacement = trial.root / "replacement"
        replacement.write_text("before")
        replacement.replace(trial.source)
        observed = {"bytes": trial.source.read_text(), "inode_changed": before.st_ino != trial.source.stat().st_ino}
        trial.check("replacement changed inode", True, observed["inode_changed"])
    else:
        trial.source.write_text("BEFORE")
        os.utime(trial.source, ns=(before.st_atime_ns, before.st_mtime_ns))
        observed = {"bytes": trial.source.read_text(), "same_size": before.st_size == trial.source.stat().st_size,
                    "same_mtime": before.st_mtime_ns == trial.source.stat().st_mtime_ns}
        trial.check("size and mtime preserved", (True, True),
                    (observed["same_size"], observed["same_mtime"]))
    trial.mutate("input.txt", {"bytes": "before", "exists": True}, observed)
    for command in ("run", "audit"):
        result = trial.call(command, {})
        if change == "same-bytes":
            trial.result(result, 0, complete=True)
        else:
            trial.result(result, 2)
            term = "No such file" if change == "delete" else "fingerprint mismatch"
            trial.check(command + " rejects changed input", True, term in result["stderr"])
    trial.check("launch count", 1, len(trial.launches()))
    trial.check("receipt preserved", baseline["receipt"], trial.receipt_snapshot())


def in_run_mutation(trial, cross_cell):
    cells = [cell("first"), cell("writer", "mutate", ())] if cross_cell else [cell("writer", "mutate")]
    trial.configure(cells)
    trial.boot({})
    result = trial.call("run", {})
    trial.mutate("input.txt during child execution", "before", trial.source.read_text())
    trial.check("mutation took place", "after", trial.source.read_text())
    trial.check("launch sequence", [config[0] for config in cells],
                [launch["cell"] for launch in trial.launches()])
    # Coverage under the original manifest requires current bindings at return.
    trial.result(result, 2)
    trial.check("run does not certify complete", False, (result["summary"] or {}).get("complete", False))
    trial.check("receipt attempt reasons",
                {"first": ["exit-zero"], "writer": ["exit-zero"]} if cross_cell
                else {"writer": ["interrupted"]}, trial.reasons())
    for command in ("audit", "run"):
        followup = trial.call(command, {})
        trial.result(followup, 2)
        stale = "first" if cross_cell else "writer"
        trial.check(command + " identifies stale binding", True,
                    f"fingerprint mismatch for {stale}" in followup["stderr"])
        trial.check(command + " preserves receipt", result["receipt"], followup["receipt"])
    trial.check("follow-ups launch no child", len(cells), len(trial.launches()))


def interruption_case(trial):
    trial.configure([cell("first"), cell("waiting", "wait", timeout=4), cell("last")])
    trial.boot({})
    trial.result(trial.interrupt({}), 130)
    trial.check("checkpoint history", {"first": ["exit-zero"], "waiting": ["interrupted"]}, trial.reasons())
    trial.result(trial.call("audit", {}), 2, complete=False, executed=1,
                 missing=["last"], unresolved=["waiting"])
    (trial.root / "ready").touch()
    trial.mutate("retry gate ready file", False, (trial.root / "ready").exists())
    trial.result(trial.call("run", {}), 0, complete=True, executed=3, **{"pass": 3})
    trial.result(trial.call("audit", {}), 0, complete=True)
    trial.check("resume history", {"first": ["exit-zero"], "waiting": ["interrupted", "exit-zero"],
                                  "last": ["exit-zero"]}, trial.reasons())
    trial.check("launch sequence", ["first", "waiting", "waiting", "last"],
                [launch["cell"] for launch in trial.launches()])


def retry_case(trial, action, reason, flag):
    timeout = 0.3 if action == "wait" else 2
    trial.configure([cell("stable"), cell("retry", action, timeout=timeout)])
    trial.boot({})
    outcome = "fail" if action == "fail" else "unknown"
    trial.result(trial.call("run", {}), 1, complete=True, executed=2, **{outcome: 1})
    trial.result(trial.call("audit", {}), 0, complete=True, **{outcome: 1})
    (trial.root / "ready").touch()
    trial.mutate("retry gate ready file", False, (trial.root / "ready").exists())
    retries = action != "fail" or flag
    args = ["--retry-failed"] if flag else []
    trial.result(trial.call("run", {}, *args), 0 if retries else 1, complete=True,
                 **{"pass": 2 if retries else 1, outcome: 0 if retries else 1})
    trial.check("attempt history", {"stable": ["exit-zero"],
                                   "retry": [reason, "exit-zero"] if retries else [reason]},
                trial.reasons())
    trial.check("launch sequence", ["stable", "retry", "retry"] if retries else ["stable", "retry"],
                [launch["cell"] for launch in trial.launches()])


def receipt_case(trial, corruption):
    trial.configure([cell("first"), cell("last")])
    trial.boot({})
    trial.result(trial.call("run", {}), 0, complete=True)
    before = trial.receipt_snapshot()
    record = read(trial.receipt)
    if corruption == "truncated":
        trial.receipt.write_text('{"version":1,"results":[')
    elif corruption == "modified-stdout":
        record["results"][0]["attempts"][0]["stdout"] = "changed"
    elif corruption == "duplicate-result":
        record["results"].append(record["results"][0])
    elif corruption == "wrong-manifest":
        record["manifest_sha256"] = "0" * 64
    elif corruption == "omit-result":
        record["results"].pop()
    if corruption != "truncated":
        write(trial.receipt, record)
    after = trial.receipt_snapshot()
    trial.mutate("receipt " + corruption, before, after)
    audit = trial.call("audit", {})
    if corruption == "omit-result":
        trial.result(audit, 2, complete=False, executed=1, missing=["last"])
        trial.result(trial.call("run", {}), 0, complete=True, executed=2)
        trial.check("launch sequence", ["first", "last", "last"],
                    [launch["cell"] for launch in trial.launches()])
        return
    diagnostics = {"truncated": "Expecting value", "modified-stdout": "result checksum mismatch",
                   "duplicate-result": "duplicate result entry", "wrong-manifest": "receipt manifest fingerprint mismatch"}
    for result in (audit, trial.call("run", {})):
        trial.result(result, 2)
        trial.check(result["command"] + " rejects corrupt receipt", True,
                    diagnostics[corruption] in result["stderr"])
    trial.check("corrupt bytes preserved", after, trial.receipt_snapshot())
    trial.check("launch count", 2, len(trial.launches()))


def empty_case(trial):
    trial.configure([])
    trial.boot({})
    for command in ("run", "audit"):
        trial.result(trial.call(command, {}), 2, complete=False, planned=0, executed=0)
    trial.check("launch count", 0, len(trial.launches()))


def matrix():
    absent, empty, alpha, beta = {}, {KEY: ""}, {KEY: "alpha"}, {KEY: "beta"}
    for label, before, after, names in [
        ("unset-stable", absent, absent, [KEY]),
        ("empty-stable", empty, empty, [KEY]),
        ("value-stable", alpha, alpha, [KEY]),
        ("unset-to-empty", absent, empty, [KEY]),
        ("empty-to-unset", empty, absent, [KEY]),
        ("unset-to-value", absent, alpha, [KEY]),
        ("value-to-unset", alpha, absent, [KEY]),
        ("empty-to-value", empty, alpha, [KEY]),
        ("value-to-empty", alpha, empty, [KEY]),
        ("value-to-value", alpha, beta, [KEY]),
        ("undeclared-drift", {**alpha, EXTRA: "first"}, {**alpha, EXTRA: "second"}, [KEY]),
        ("empty-allowlist", alpha, beta, []),
    ]:
        yield "env-" + label, environment_case, (before, after, names)
    for change in ("same-size-mtime", "delete", "same-bytes"):
        yield "input-" + change, input_case, (change,)
    yield "input-mutation-same-cell", in_run_mutation, (False,)
    yield "input-mutation-earlier-cell", in_run_mutation, (True,)
    yield "sigint-resume", interruption_case, ()
    for label, action, reason, flag in [
        ("fail-default", "fail", "exit-nonzero", False),
        ("fail-flag", "fail", "exit-nonzero", True),
        ("timeout-default", "wait", "timeout", False),
        ("signal-default", "signal", "signal", False),
    ]:
        yield "retry-" + label, retry_case, (action, reason, flag)
    for change in ("truncated", "modified-stdout", "duplicate-result", "wrong-manifest", "omit-result"):
        yield "receipt-" + change, receipt_case, (change,)
    yield "empty-plan", empty_case, ()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "REPORT.json")
    args = parser.parse_args()
    started = time.monotonic()
    runner_hash = hashlib.sha256(RUNNER.read_bytes()).hexdigest()
    variants = []
    with tempfile.TemporaryDirectory(prefix="runner-stress-") as directory:
        for name, exercise, parameters in matrix():
            root = Path(directory) / name
            root.mkdir()
            trial = Trial(root)
            error = None
            try:
                exercise(trial, *parameters)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            variants.append({
                "id": name, "planned": {"scenario": exercise.__name__, "parameters": parameters,
                                        "spec": trial.configuration},
                "actual": trial.operations, "checks": trial.checks, "harness_error": error,
                "verdict": "harness-error" if error else
                           "matched" if all(check["matched"] for check in trial.checks) else "counterexample",
            })
    runner_unchanged = hashlib.sha256(RUNNER.read_bytes()).hexdigest() == runner_hash
    counts = {status: sum(row["verdict"] == status for row in variants)
              for status in ("matched", "counterexample", "harness-error")}
    write(args.output, {
        "schema_version": 1, "runner": "../campaign-runner/campaign.py", "runner_sha256": runner_hash,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "runner_unchanged": runner_unchanged, "python": sys.version.split()[0], "platform": platform.system(),
        "command_timeout_seconds": CALL_LIMIT, "duration_seconds": round(time.monotonic() - started, 3),
        "variant_count": len(variants), "counts": counts, "variants": variants,
    })
    print(json.dumps({"variants": len(variants), **counts, "runner_unchanged": runner_unchanged}))
    if counts["harness-error"] or not runner_unchanged:
        return 2
    return int(bool(counts["counterexample"]))


if __name__ == "__main__":
    sys.exit(main())

```
