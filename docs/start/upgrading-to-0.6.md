---
doc_type: migration
audience:
  - library-maintainer
  - ci-owner
level: intermediate
lifecycle: migration
generated: false
---

# Upgrading from 0.5 to 0.6

> **Status: `0.6.0` is not published yet.** The latest published release is
> **v{{ latest_published_version }}**. This page describes what changes when
> `0.6.0` ships; it is written against `main`, which is what this site is
> built from. Check `abicheck --version` (the executable actually on `PATH`)
> and `CHANGELOG.md` before following it.

This page is the **authoritative** 0.5 → 0.6 migration. It is organised
around the edits you have to make, not around the internal history that
produced them: you should not need to read an ADR to upgrade.

0.6 is a **pre-1.0 surface reset**, not a feature release. Most of what
follows is a removal with a named replacement. There is no alias window and
no deprecation shim anywhere on this page — a retired spelling exits `64`
("No such option" / "No such command"), which is deliberately outside the
verdict space so a stale script fails loudly instead of scoring something.

**Do this first:** run your existing command line once against 0.6. Exit
`64` means you are on this page; anything else means the invocation still
parses, and the sections below tell you whether it still *means* the same
thing.

- [A. Commands and workflows](#a-commands-and-workflows)
- [B. Report output](#b-report-output)
- [C. Evidence and configuration](#c-evidence-and-configuration)
- [D. Python API](#d-python-api)
- [E. Machine output (report/snapshot schemas)](#e-machine-output-report-and-snapshot-schemas)

---

## A. Commands and workflows

### The root command surface

0.6's root surface is `dump`, `compare`, `deps`, `compat`, `aggregate`,
`project`. Three roles, and they do not overlap:

| Command | The question it answers |
|---|---|
| `compare OLD NEW` | How did this library's surface evolve between two builds? **The main entry point.** |
| `compare --no-baseline NEW` | What can be established about *this one build* alone? An **audit** — see [§4](#a1-scan-is-retired) and [Understand your first report](first-report.md#audit-runs-no-baseline). |
| `dump` | Capture one build's evidence as a snapshot, for later use as a baseline or operand. |
| `deps` | What does this binary need from its runtime environment, and does that environment supply it? |

### A1. `scan` is retired

`abicheck scan` no longer exists. It exited the root surface outright — no
alias, no shim. Both of its modes moved onto `compare`:

| 0.5 | 0.6 |
|---|---|
| `abicheck scan NEW --against OLD` | `abicheck compare OLD NEW` |
| `abicheck scan NEW` (no `--against`) | `abicheck compare --no-baseline NEW` |

**This is more than a command-name substitution.** Three things to check
before you treat the migration as done:

1. **The audit does not gate by default.** `scan` with no `--against` gated
   on its own findings. `compare --no-baseline` is advisory unless you pass
   `--severity-preset` — see [§A2](#a2-re-arming-an-audit-gate). This is the
   single most likely way to silently lose CI enforcement in this upgrade.
2. **`--depth` no longer escalates itself.** `scan`'s `--source-method auto`
   could escalate a high-risk diff to `build`/`source` evidence on its own.
   That is gone. Omitting `--depth` resolves to the fixed `headers` rung, the
   same default `compare` has always had. If you relied on escalation, pin
   the rung: `--depth build` or `--depth source`.
3. **`scan`'s evidence axes are gone, not renamed.** `--mode`,
   `--source-method s0…s6` and `--max` are not accepted by `compare`. The
   `s0…s6` vocabulary is internal now and has no public entry point at all.
   The mapping, and the two rungs that have **no** user-facing equivalent
   (`s4`'s cheap graph-only level, `pr-deep`'s whole-library reachability
   graph), is in
   [Migrating to the Current CLI](../use/companion-commands.md#removed-scan-axes-s0s6-mode-source-method-max).

`scan`'s exit codes are documented historically in
[Exit Codes → `abicheck scan` (retired)](../reference/exit-codes.md#abicheck-scan-retired).
Do not copy an invocation from that section.

### A2. Re-arming an audit gate

A `--no-baseline` audit reports candidate-side findings — hygiene and
cross-source observations about one build. It never reports an addition, a
removal or a compatibility verdict, because it has nothing to compare
against. Consequently it has **no** `2`/`4` compatibility exit.

Its own gate is opt-in, on one switch:

```bash
# Advisory (default): findings are reported, exit stays 0.
abicheck compare --no-baseline build/libfoo.so -H include/

# Enforced: exit 3 on the first BREAKING/API_BREAK finding.
abicheck compare --no-baseline build/libfoo.so -H include/ \
  --severity-preset default
```

!!! danger "`--policy` alone does **not** arm the audit gate"
    A `--policy` document that promotes a finding to `break` changes the
    finding's verdict and nothing else. `--severity-preset` is the sole
    switch that arms this axis, and `--severity-preset info-only` explicitly
    disarms it. A `.abicheck.yml` `severity:` block does **not** arm it
    either — the axis reads the CLI flag.

    Verified on a candidate exporting an undeclared symbol, with a policy
    promoting `exported_not_public` to `break`:

    | Invocation | Finding verdict | Exit |
    |---|---|---|
    | `--policy pol.yaml` | `BREAKING` | `0` |
    | `--policy pol.yaml --severity-preset default` | `BREAKING` | `3` |
    | `--policy pol.yaml --severity-preset info-only` | `BREAKING` | `0` |

Exit `3` is this axis's own code. It is deliberately **not** `2` or `4`: an
audit structurally cannot report a compatibility break, so it must never
emit a code a CI script reads as one. See
[Exit Codes → the audit-gate axis](../reference/exit-codes.md).

### A3. Commands removed before 0.6

`baseline`, `collect`, `merge`, `recommend-collect-mode`, `debian-symbols`,
`doctor`, `config`, `init`, `surface-report`, `graph`, `pr-comment`,
`suggest-suppressions` and `probe` were removed in the 0.5-era CLI reset and
are still gone. `appcompat` and `plugin-check` became `compare --used-by` /
`compare --required-symbol`. If you are coming from a release older than 0.5,
read [Migrating to the Current CLI](../use/companion-commands.md) as well —
it carries those per-release mappings.

### A4. Consumer scoping enriches, it does not replace

`--used-by` / `--required-symbol(s)` add a consumer-impact assessment
**beside** the full-library result. They never narrow the comparison and
never become the run's verdict or exit code.

Verified: a library with a genuine exported-symbol removal that the selected
consumer does not use reports `verdict: BREAKING`, exits `4`, and carries
`consumer_scope.verdict: COMPATIBLE` alongside. The one thing that *did*
change from 0.5 is the JSON shape — see [§E](#e-machine-output-report-and-snapshot-schemas).

---

## B. Report output

### B1. One repeatable export flag

Every report-rendering flag `compare` had in 0.5 (`--format`, `--output`,
`--output-dir`, `--secondary-format`, `--secondary-output`, `--stat`)
collapsed into one repeatable option:

```
-o FORMAT=DESTINATION
```

`FORMAT` is `json`, `markdown`, `sarif`, `html`, `junit`, `review` or
`oneline`. `DESTINATION` is a file path, or `-` for stdout. The default is
`markdown=-`.

| 0.5 | 0.6 |
|---|---|
| `--format json` | `-o json=-` |
| `--format json --output r.json` | `-o json=r.json` |
| `--format md --secondary-format sarif --secondary-output r.sarif` | `-o markdown=- -o sarif=r.sarif` |
| `--output-dir reports/` | `-o json=reports/` (per-component export; `json` only) |
| `--stat` (compact summary) | `-o oneline=-` for the human one-liner, `-o json=-` for the machine summary |

Repeating `-o` is safe by construction: every export renders the same one
completed analysis, so asking for more artifacts never re-runs anything and
never moves the verdict or the exit code. A `DESTINATION` ending in `/`, or
naming an existing directory, is a per-component export (`json` only). A
directory/package release comparison renders `json`/`markdown`/`junit`/
`oneline` only.

!!! warning "`dump -o` is a different option"
    `dump`'s `-o/--output` takes a **path**, not `FORMAT=DESTINATION` —
    `dump` emits one artifact (a snapshot), not a choice of report formats.
    `abicheck dump libfoo.so -H include/ -o snap.json` is unchanged and must
    **not** be rewritten to `-o json=snap.json`. The snapshot's storage
    envelope is selected separately with `--compression`.

### B2. Presentation switches that became automatic or moved

| 0.5 flag | 0.6 |
|---|---|
| `--report-mode leaf\|impact` | `--view impact` / `--view root-cause`. **`leaf` was removed outright** — see [§E](#e-machine-output-report-and-snapshot-schemas). |
| `--show-only <filter>` | `--view show=<tokens>` (severity / element / action tokens; AND across dimensions, OR within one) |
| `--show-impact` | `--view impact` |
| `--show-filtered` | **Removed — now automatic.** The scope/reconciliation ledger, the pattern-modulation ledger and the `--suppress` audit are always reported (ADR-067). There is nothing to switch on. |
| C++ demangling switches | **Removed — now automatic.** Human output always demangles and always keeps the exact mangled name beside it. |
| `--exit-code-scheme` | **Removed.** The scheme is derived: severity-aware when a severity setting is in effect, legacy otherwise. See [CI Gating → the two exit-code schemes](../use/ci-gating.md#the-two-exit-code-schemes). |
| `--profile {ci-gate,release-cut,quick}` | **Removed, no replacement token.** State `--depth`, `-o` and `--severity-preset` independently — a rendering choice may not carry a gate setting. `quick` is `-o oneline=-`. |

`--view` never changes the verdict, the findings or the exit code. If a 0.5
script used `--report-mode`/`--show-only` to *shrink what CI gated on*, that
was never what those flags did; move the decision into `--policy` or the
`severity:` block.

---

## C. Evidence and configuration

0.6 finishes a split that 0.5 started: a **per-run choice** stays a CLI
flag, a **stable project/toolchain property** moves into `.abicheck.yml`.
There is no CLI override for a config-only key — no hidden flag, nothing to
pass that wins over the file. A removed spelling exits `64`.

**These are YAML keys, not positional arguments.** Writing
`abicheck compare old.so new.so scope.public_symbols my_sym` is not a
supported spelling of anything; it is two stray operands.

### C1. Compiler / frontend

| 0.5 flag | 0.6 `.abicheck.yml` key |
|---|---|
| `--ast-frontend` (and `--old-ast-frontend`/`--new-ast-frontend`) | `compile.frontend: castxml\|clang\|hybrid\|auto` |
| `--allow-ast-frontend-fallback` | `compile.ast_frontend_fallback: true` |
| `--allow-unsupported-castxml` | `compile.allow_unsupported_castxml: true` |
| `--gcc-path` / `--gcc-prefix` (`--compiler`/`--compiler-prefix`) | `compile.compiler:` — one merged key; a trailing `-` is a toolchain prefix, anything else a compiler path |
| `--gcc-option` / `--compiler-option` (repeatable) | `compile.options:` (a YAML list) |
| `--sysroot` | `compile.sysroot:` |
| `--nostdinc` | `compile.nostdinc: true` |
| `--lang c` | `compile.lang: c` |
| `--frontend-context` | `compile.frontend_context: host\|device` |

There is no per-side spelling of any of these. `--ast-frontend old=`/`new=`
does not exist on `compare` in 0.6, because there is no CLI spelling left to
be sided.

### C2. Debug evidence

| 0.5 flag | 0.6 |
|---|---|
| `--debug-format dwarf` | `debug.format: dwarf` |
| `--dwarf-only` | `debug.dwarf_only: true` |
| `--debuginfod` | `debug.debuginfod: true` |
| `--debuginfod-url URL` | `debug.debuginfod_url: URL` |
| `dump --pdb-path` | `debug.pdb_path: PATH` |
| `--debug-root d1` / `--debug-info1 x` | `--debug-info old=d1 --debug-info new=x` |

`--debug-info` is **still a flag** — the coarse per-run debug-artifact
override. It is side-aware and it now carries the whole separate-debug-info
role: a directory to search, a detached `.debug` file, or a debug *package*
are three transports of one input, told apart by content rather than by
filename.

!!! warning "There is no per-side PDB input"
    `--pdb-path` does not exist on any command — `compare --pdb-path` exits
    `64`. `debug.pdb_path` in `.abicheck.yml` is the only spelling the PE
    dump path consumes, and it is **a single value, not side-aware**: you
    cannot point old and new at different PDBs. `--debug-info` does not
    cover the gap — it refuses a named `.pdb` rather than accepting it as a
    silent no-op. A two-sided PE comparison needing distinct PDBs has no
    supported invocation today.

### C3. Header / development-package inputs

`-H/--header` is the single header input. It accepts a header file, a
directory, **or a development package** (RPM/Deb/tar) — recognised from the
operand's content, not its name.

| 0.5 flag | 0.6 |
|---|---|
| `--old-header v1/f.h --new-header v2/f.h` | `--header old=v1/f.h --header new=v2/f.h` |
| `--devel-pkg1 p --devel-pkg2 q` | `-H old=p -H new=q` |
| `--public-header` | `-H/--header` (provenance is derived from the same set) |
| `--public-header-dir` | `scope.public_header_dirs:` (a YAML list) |
| `--public-symbol` / `--public-symbols-list` | `scope.public_symbols:` (a YAML list) |

Repeat the flag per side; `--header old=a new=b` is wrong (the second token
is not a value). A bare `-H`/`-I` still means both sides. `both=` is the
escape hatch for a path that literally begins `old=`/`new=`.

### C4. Build / probe evidence

| 0.5 flag | 0.6 |
|---|---|
| `--old-build-info b1 --new-build-info b2` | `--build-info old=b1 --build-info new=b2` |
| `--probe-matrix-old m1 --probe-matrix-new m2` | `--build-info old=m1 --build-info new=m2` |
| `--probe-matrix m` | `--build-info m` — a probe-matrix snapshot is one of `--build-info`'s transports |
| `--compile-db` / `--build-query` | `build.compile_db:` / `build.query:` |
| `--reconcile-build-context` | **Removed — now automatic.** Build-context reconciliation runs whenever build context is present; clearing a false positive was never something to opt into. |

There is **no CLI to generate** a probe matrix any more (`probe` was
removed). `--build-info` consumes a previously captured one.

### C5. Assurance and scope

| 0.5 flag | 0.6 `.abicheck.yml` key |
|---|---|
| `--require-complete-analysis` | `assurance.require_complete: true` |
| `--on-incomplete-scope block` | `scope.on_incomplete: block` |
| `--collapse-versioned-symbols` | `scope.collapse_versioned_symbols: true` |
| `--show-redundant` | `scope.show_redundant: true` |
| `--strict-suppressions` | `suppression.strict: true` |
| `--require-justification` | `suppression.require_justification: true` |
| `--env-matrix` | the `deployment:` block |

`--scope-public-headers` / `--no-scope-public-headers` are **not** demoted:
they remain the everyday on/off switch for public-surface scoping.

`assurance.require_complete` is an orthogonal exit axis: an
`analysis_assurance.status` other than `complete` contributes exit `1`,
folded with `max`. Without the key, assurance is still always computed and
reported in `-o json=…`; it just never reaches the exit code. See
[Config File Reference](../reference/config-file.md).

Worked example:

```yaml
# .abicheck.yml
compile:
  frontend: clang
  lang: c
  sysroot: /opt/sysroots/aarch64
  compiler: aarch64-linux-gnu-
  options: [-march=armv8-a]
debug:
  format: auto
  dwarf_only: false
assurance:
  require_complete: true
scope:
  public: true
  public_symbols:
    - my_asm_stub
```

```bash
abicheck compare libfoo.so.1 libfoo.so.2 \
  --header old=v1/ --header new=v2/ --config .abicheck.yml
```

---

## D. Python API

The supported import surface is `abicheck.service`. The request/result types
are defined in `abicheck.workflows.contracts` and
`abicheck.workflows.request_inputs`, but **import them from
`abicheck.service`** — that is the stable re-export point:

```python
from abicheck.service import (
    CompareRequest, CompareResult, DumpRequest, InputSpec, run_compare_request,
)
```

### D1. `CompareResult` instead of a tuple

`run_compare`/`run_compare_request` returned a bare
`tuple[DiffResult, AbiSnapshot, AbiSnapshot]` before 0.6. They now return a
`CompareResult` dataclass, which breaks positional unpacking:

```python
# Before
result, old_snapshot, new_snapshot = run_compare(...)

# After
result, old_snapshot, new_snapshot = run_compare(...).as_tuple()
# ...or, preferably, by attribute:
res = run_compare(...)
res.diff, res.old_snapshot, res.new_snapshot, res.suppression
```

Attribute access to the three original values was never broken. A dataclass
is what lets future fields be added without repeating this migration.

### D2. The scan APIs are gone

`ScanRequest`, `ScanResult`, `run_scan`, `run_audit` and `run_scan_set` were
removed, along with the `abicheck.service_scan` module. A baseline
comparison — what `run_scan(ScanRequest(baseline=...))` did — is a
`CompareRequest`. `estimate_scan` survives, but takes an `InputSpec` plus
run-scoped level arguments rather than a request.

### D3. There is no typed one-sided audit entry point

`compare --no-baseline` has **no** `CompareRequest`-shaped Python
equivalent in 0.6.
`abicheck.workflows.no_baseline_compare.run_no_baseline_compare` exists but
is not re-exported from `abicheck.service` and is not part of the documented
API surface. Until a
typed entry point lands, call the CLI. Do not assume parity here.

### D4. Default changes worth re-checking

- **`include_dependencies` now defaults to `False` everywhere.** In 0.5 a
  typed-API caller that omitted the field got the *unfiltered* declaration
  surface while the identical CLI invocation got the filtered one — and the
  two were not even comparable (`scope_mismatch`, no verdict).
  `InputSpec.include_dependencies`, `run_dump` and the CLI now share one
  default:
  toolchain/system declarations are excluded. Pass `True`, or
  `compare/dump --include-system-declarations`, for the old typed-API
  behaviour.
- **Contract evaluation is authoritative.** If you already pass
  `contract_evaluation=True`/`--contract`, relevance now decides whether a
  finding reaches policy at all, instead of being a shadow annotation. The
  *compatibility verdict* can only move to the same or a **less** severe
  value; the *process exit* can still rise to `1` via the separate
  contract-coverage axis. A run without `--contract` is unaffected.

Full detail: [Python API](../use/python-api.md).

---

## E. Machine output (report and snapshot schemas)

!!! note "Three version numbers, three meanings"
    | Number | 0.5.0 | 0.6 (`main`) | What it versions |
    |---|---|---|---|
    | Product version | `0.5.0` | `0.6.0` | the distribution |
    | `report_schema_version` | `2.4` | `5.0` | the `compare` JSON report |
    | `schema_version` (snapshot) | `8` | `48` | one `dump` snapshot |

    They move independently. Never infer one from another.

### E1. Report schema `2.4` → `5.0`

Three MAJOR bumps sit between the released 0.5 report and 0.6. A consumer
pinned to `2.x` needs all three:

**`3.0` — consumer-scope keys removed.** `--used-by`/`--required-symbol(s)`
no longer swap `verdict`/`severity`/`run_outcome`/`summary` for the
consumer's own scoped result; those four always describe the full-library
result now. The `full_verdict` / `full_severity` / `full_run_outcome` /
`full_summary` keys that the swap used to populate are **removed**. Read the
new, purely informational `consumer_scope` object instead (`verdict`,
`scope`, and under the severity scheme `exit_code`/`exit_code_scheme`).
`used_by` and `required_symbol_contract` are unchanged.

**`4.0` — `summary.compatible_additions` changed meaning.** No key was added,
removed or retyped; the number is different. It now excludes
`quality_issues` instead of counting every `COMPATIBLE` finding. The old
value equals `compatible_additions + quality_issues`.

**`5.0` — `leaf` mode removed; per-finding fields added.**

- **Removed:** the `leaf` report mode and with it the `leaf_changes` and
  `non_type_changes` keys. `root_causes` / `root_cause_count` is the
  supported grouping. (Retired on a measurement: across the 129 catalog
  library pairs that build in this environment, `leaf` and `root-cause`
  exposed the identical finding set in all 93 cases that had findings, and
  `leaf`'s headline section was empty in 40 of them.)
- **Added:** a per-finding `entity` field — the canonical
  `function`/`variable`/`type`/`enum`/`binary`/`build`/`source`/`analysis`
  the change catalog declares — emitted beside `operation`.
- **Changed:** a finding's `operation` value **may move** for kinds the old
  name-suffix heuristic classified wrongly. It is now read off the same
  registration that declares the kind's verdict. Only the display dimension
  moves — no verdict, gate, exit code, coverage contribution or assurance
  value depends on it.
- **Changed:** `demangled_symbol` is now resolved for *every* Itanium-mangled
  finding, not only `ELF_ONLY`-visibility ones. Additive in practice: the key
  appears on more findings, never with a different meaning. **The mangled
  name remains the symbol's identity** — `symbol` is still the exact mangled
  spelling; `demangled_symbol` is a display companion. Do not key on it.

The `--no-baseline` audit is a **separate document with a separate schema**
(`audit_report_schema_version`, not `report_schema_version`) —
[`audit_report.schema.json`](../reference/schemas/v1/audit_report.schema.json).
Its `verdict` is always `null`, meaning "no comparison was performed". That
is **not** `compare_report`'s null verdict, which means "the comparability
gate rejected this pair" and requires a `reason` alongside. A consumer that
treats the two identically will read a `NOT_COMPARABLE` pair as an audit, or
vice versa. It also carries `old_acquisition_state: "declared_absent"`, an
empty `changes` array, and an `exit_axes` object giving each orthogonal
axis's own contribution.

### E2. Snapshot schema `8` → `48`: the wire format changed

This is the one 0.6 change most likely to break an **external** JSON
consumer, and it is invisible to `abicheck` itself.

At schema **v42** the on-disk document changed shape. `dump` no longer writes
a flat object whose top level is `library`/`version`/`functions`/…; it writes
a **sectioned envelope**:

```json
{
  "schema_version": 48,
  "sections": {
    "binary":       {"section_kind": "...", "section_schema_version": 1, "payload": {…}},
    "declarations": {"section_kind": "...", "section_schema_version": 1, "payload": {…}},
    "types":        {"…": "…"}
  },
  "section_schema_versions": {"binary": 1, "declarations": 1, "types": 1, "…": 1}
}
```

Two consequences, and they are not the same consequence:

- **abicheck reads both.** `load_snapshot`/`snapshot_from_dict` unwrap the
  envelope transparently and still read an older flat `.abi.json` exactly as
  before. Your stored baselines keep working.
- **A hand-rolled JSON consumer does not.** Anything that did
  `json.load(f)["functions"]` reads an absent key against a 0.6 snapshot. Use
  the public loader instead of assuming a physical layout:

  ```python
  from abicheck.serialization import load_snapshot
  snap = load_snapshot("baseline.abi.json")
  snap.library, snap.functions, snap.types
  ```

A **pre-0.6 abicheck cannot read a 0.6 snapshot**: v42 was bumped
specifically so an older reader hits the hard-rejection path instead of
silently reading every field as empty. Regenerate baselines with the version
that will consume them, or keep the producer and consumer on the same
release.

### E3. Loading an old snapshot does not upgrade its evidence

Reading a pre-0.6 snapshot succeeds, and warns. Verified against a real
schema-8 document:

```text
UserWarning: Snapshot schema_version 8 predates this abicheck's schema_version 48:
header_cv_facts_reliable, param_kind_facts_reliable are marked unreliable on this
snapshot, so the affected detectors will decline to trust these stale facts rather
than risk a false positive purely from this tool upgrade.
```

**Re-saving does not clear this.** Loading and re-saving stamps
`schema_version: 48` and the new envelope, but the warning persists (now
saying so explicitly) and the underlying facts stay `unknown` — the 0.5
extractor never collected them, and no serialization step can invent them.
If you need the evidence, re-run `dump` against the artifact. See
[Snapshot Format → Reading an older snapshot](../reference/snapshot-format.md).

### E4. Aggregate schema

`aggregate_schema_version` `1.2` added `finding_matrix`; `1.3` added the
top-level `contract_coverage` block and a per-target
`contract_coverage_exit`. Both are purely additive — a consumer reading only
`gate`/`coverage`/`compatibility`/`targets` needs no changes, but one
asserting an *exact* key set will fail. See
[Aggregate Reports](../use/aggregate-reports.md).

---

## Exit-code changes to re-check

Exit codes did not simply gain values; two axes are new and one is
orthogonal in a way a 0.5 script will not expect.

| Exit | When | New in 0.6? |
|---|---|---|
| `0`/`2`/`4` | the compatibility verdict (legacy scheme) | no |
| `0`/`1`/`2`/`4` | severity-aware scheme, in effect when `--severity-preset` or a config `severity:` block is | no |
| `1` | **also** incomplete `--contract` coverage, and **also** incomplete analysis assurance under `assurance.require_complete` | yes |
| `3` | the `--no-baseline` audit gate | yes |
| `64` | invalid invocation — a retired flag or command | no |

The orthogonal axes fold with `max`: they raise a clean `0`, and never lower
a `2`/`4`. Under the legacy scheme, `1` can *only* mean an orthogonal axis.
To tell them apart in JSON, read `contract_coverage_exit_contribution` and
the `exit` block's own `reasons`, not the process exit alone.

A script that branched on "exit `1` means a severity error" needs updating.
A script that treated any nonzero exit as a break will now fail on an
incomplete-evidence run that found nothing wrong.

---

## What did **not** change

- `dump -o PATH` and the `dump` operand shape.
- `-H`/`-I` meaning "both sides".
- `--version old=`/`new=` defaults (`old`/`new`).
- The `compat` drop-in interface. It is frozen; ABICC-compatible scripts are
  unaffected by everything on this page.
- The GitHub Action's per-side inputs (`old-header`, `new-header`,
  `old-version`, `debug-info1`, `devel-pkg1`, …). The wrapper maps them to
  the current flags internally. The Action's own `mode: scan` input **was**
  retired — see
  [GitHub Action → migrating from `mode: scan`](../use/github-action.md#migrating-from-mode-scan).

## See also

- [Understand your first report](first-report.md) — how to read a result before trusting its exit code
- [Migrating to the Current CLI](../use/companion-commands.md) — the pre-0.5 and 0.5-era mappings
- [Exit Codes](../reference/exit-codes.md) — the full per-command matrix
- [Snapshot Format](../reference/snapshot-format.md) — the current on-disk document
- [Python API](../use/python-api.md)
- `CHANGELOG.md` — the exhaustive, fragment-by-fragment record
