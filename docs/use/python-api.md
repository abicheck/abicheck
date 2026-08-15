---
doc_type: how-to
audience:
  - library-maintainer
level: intermediate
canonical_for:
  - python-api
lifecycle: active
generated: false
---

# Python API

abicheck's functionality is available as a Python library through the
`abicheck.service` module. This is the **supported public entry point** — the
same Tier-2 service layer the CLI calls. Front-ends should route through
`service` rather than importing the internal `abicheck.checker` core
directly (ADR-037). Agent and script integrations use this API (or the
CLI's structured JSON/SARIF output) directly — there is no separate
protocol server.

> **Install.** `pip install abicheck`. Native-binary header analysis also needs
> `castxml` and a C++ compiler; without them, binary-only mode still works. See
> [Getting Started](../start/getting-started.md).

## Compare two libraries

`run_compare` is the one-call entry point: it resolves both inputs to snapshots,
runs the comparison, and returns the classified result.

```python
from pathlib import Path
from abicheck.service import run_compare

result = run_compare(
    old_input=Path("libfoo.so.1"),
    new_input=Path("libfoo.so.2"),
    old_headers=[Path("include/v1/foo.h")],
    new_headers=[Path("include/v2/foo.h")],
)

print(result.diff.verdict)       # Verdict.BREAKING, Verdict.COMPATIBLE, ...
print(len(result.diff.changes))  # number of detected changes
for change in result.diff.changes:
    print(change.kind, change.name)
```

`run_compare` returns a `CompareResult` — `diff` (the `DiffResult`),
`old_snapshot`, `new_snapshot`, and the resolved `suppression` list. It raises
`SnapshotError` if an input cannot be loaded and `ValidationError` for an
unrecognised input format (both from `abicheck.errors`).

!!! note "Changed in 0.6"
    `run_compare` and `run_compare_request` returned a bare
    `tuple[DiffResult, AbiSnapshot, AbiSnapshot]` before 0.6. A struct can gain
    a field without breaking positional callers, which a tuple cannot — so the
    typed result became the only shape rather than a second one alongside it
    (ADR-055 D2). To migrate a positional caller in one line:

    ```python
    result, old_snapshot, new_snapshot = run_compare(...).as_tuple()
    ```

### Common keyword arguments

`run_compare` is a keyword shim over a typed `CompareRequest`; the arguments you
will reach for most often:

| Argument | Type | Default | Purpose |
|----------|------|:-------:|---------|
| `old_input` / `new_input` | `Path` | — | Binary (`.so`/`.dll`/`.dylib`) or a `.abi.json` snapshot |
| `old_headers` / `new_headers` | `list[Path]` | `None` | Public headers for `L2` API analysis (`-H` on the CLI) |
| `old_includes` / `new_includes` | `list[Path]` | `None` | Extra include dirs passed to the header parser (`-I`) |
| `old_version` / `new_version` | `str` | `""` | Version labels recorded in the snapshots |
| `lang` | `str` | `"c++"` | Header language mode (`"c++"` or `"c"`) |
| `frontend` | `str` | `"auto"` | Header-AST frontend: `"auto"`, `"castxml"`, `"clang"`, or `"hybrid"` (runs castxml and clang together and merges them). (A fifth value, `"android"`, is source-ABI-only — it needs source inputs and is rejected by `run_compare`, which has no source-input path.) |
| `policy` | `str` | `"strict_abi"` | Built-in policy profile (`strict_abi`, `sdk_vendor`, `plugin_abi`) |
| `policy_file_path` | `Path` | `None` | Custom YAML policy file |
| `suppress` | `Path` | `None` | Suppression file (YAML or ABICC format) |
| `scope_to_public_surface` | `bool` | `True` | Restrict findings to the public ABI surface |
| `enable_debuginfod` | `bool` | `False` | Resolve debug info via debuginfod |

The table above is the common subset, not the full surface. `run_compare` also
takes per-side PDB paths, debug roots, forced public symbols, and pattern
verdicts; for those, build a `CompareRequest`/`InputSpec` directly and call
`run_compare_request`. See the [Python API Reference](../reference/python-api-reference.md)
for the complete, generated argument/field list of every name in `service.__all__`.

## Work with snapshots directly

To produce a snapshot once and reuse it (for example, to build a baseline), use
`resolve_input` (auto-detects the input type) or `run_dump` (native binaries),
then `compare_snapshots` to classify two already-loaded snapshots.

```python
from pathlib import Path
from abicheck.service import resolve_input, compare_snapshots
from abicheck.serialization import save_snapshot, load_snapshot

# Build and persist a baseline snapshot.
baseline = resolve_input(Path("libfoo.so.1"), headers=[Path("include/foo.h")], version="1.0")
save_snapshot(baseline, Path("baseline.abi.json"))

# Later — compare a fresh build against the saved baseline.
old = load_snapshot(Path("baseline.abi.json"))
new = resolve_input(Path("build/libfoo.so"), headers=[Path("include/foo.h")])
result = compare_snapshots(old, new, policy="strict_abi")
print(result.verdict)
```

`compare_snapshots` returns a `DiffResult`. Unlike `run_compare`, it works on
**already-loaded objects**, not file paths: `policy` is a built-in profile name,
but a custom policy file is passed as a loaded `PolicyFile` via `policy_file=`,
and suppressions as a loaded `SuppressionList` via the `suppression=` argument
(scoping keywords such as `scope_to_public_surface` match `run_compare`). Use
`load_suppression_and_policy` to turn paths into those objects:

```python
from abicheck.service import load_suppression_and_policy, compare_snapshots

suppression, policy_file = load_suppression_and_policy(
    suppress=Path("suppressions.yaml"),
    policy_file_path=Path("policy.yaml"),
)
result = compare_snapshots(old, new, suppression, policy_file=policy_file)
```

If you only have file paths and don't want to pre-load them, call `run_compare`
(or `run_compare_request`) instead — it accepts `suppress=`/`policy_file_path=`
as paths and does the loading for you. Snapshots are serialised as `.abi.json`;
see [Snapshot Format](../reference/snapshot-format.md) for the on-disk contract
and current `schema_version`, [Output Formats](output-formats.md) for the
comparison-report shape, and [Baseline Management](baseline-management.md) for
the baseline workflow.

## Render results

`render_output` turns a `DiffResult` into any of the supported report formats,
so you can reuse abicheck's exact reporter output from your own code.

```python
from abicheck.service import render_output

report = render_output("sarif", result, old, new)
Path("report.sarif").write_text(report)
```

Supported `fmt` values: `"markdown"` (alias `"md"`), `"json"`, `"sarif"`,
`"html"`, `"junit"`, and `"review"` (the compact review digest). `render_output`
raises `ValidationError` for an unrecognised format.

## Typed request API

`run_compare`/`run_dump` are convenience shims — keyword arguments in,
typed result out. Underneath, this Python API resolves through the **same
typed request objects** the native CLI does: `DumpRequest`, `CompareRequest`,
and `ScanRequest`. The native `compare` CLI resolves through
`CompareRequest` too (`cli_resolve.py` assembles it from `compare`'s loose
arguments and hands it to `resolve_compare_request`); the native `dump` CLI
is the one exception — it still runs its own `dump_cmd` argument path
rather than building a `DumpRequest` (see G33 Phase 5's note in `AGENTS.md`
for what that migration still needs). Reaching for the typed request
directly buys you two things a keyword shim can't:

- **The identical validation *rules*** across every front end that
  actually builds the typed request — `compare`/`scan` (both CLI and typed
  API) and the typed API's `run_dump_request` — reject a bad combination of
  fields the same way regardless of which one built the request. (The
  native `dump` CLI is the exception noted above: since it doesn't build a
  `DumpRequest`, this shared-validation guarantee doesn't cover it.) How a
  rejection *surfaces* still differs per transport: calling the typed API
  directly raises `ValidationError`; the CLI translates the equivalent
  failure into its own usage-error/exit-code behavior. The **rule** is
  shared, not the **exception type** — see the parity table below for how
  each transport represents the same failure.
- **Repeatable configuration** — build one `CompareRequest` once (from a
  config file, a test fixture, a stored preset) and reuse it, rather than
  re-threading a dozen keyword arguments.

| Operation | Convenience API | Typed API | Result |
|---|---|---|---|
| Dump | `run_dump(...)` | `run_dump_request(DumpRequest(...))` | `AbiSnapshot` |
| Compare | `run_compare(...)` | `run_compare_request(CompareRequest(...))` | `CompareResult` |
| Scan | *(none — always typed)* | `run_scan(ScanRequest(...))` | `ScanResult` |

### `DumpRequest`

```python
from pathlib import Path
from abicheck.api_types import DumpRequest, InputSpec
from abicheck.service import run_dump_request

request = DumpRequest(
    input=InputSpec(
        path=Path("libfoo.so"),
        headers=[Path("include/foo.h")],
        version="1.0",
    ),
    depth="headers",     # a floor, not a target — see below
)
snapshot = run_dump_request(request)
```

Key `DumpRequest` fields, beyond the `InputSpec` it wraps (`path`,
`headers`, `includes`, `version`, `pdb`, `debug_roots`,
`include_dependencies`, `sources`, `build_info`, `dump_manifest`,
`compile`, `public_header_dirs`):

| Field | Meaning |
|---|---|
| `depth` | `binary`/`headers`/`build`/`source` — an explicit value is an **enforced floor**: `run_dump_request` raises `ValidationError` if the resolved snapshot's evidence doesn't actually reach it, the same guarantee the CLI's `dump --depth` gives via `DumpDepthNotSatisfiedError` (a different exception type, since Tier-2 has no `ClickException` concept — same guarantee, different vocabulary). |
| `frontend` | Header-AST frontend: `auto`/`castxml`/`clang`/`hybrid`, plus a fifth, source-ABI-only value `android` — rejected unless the request also carries source evidence (`has_sources=True` or `sources`/`build_info` set), since `android` has no header-AST extraction path of its own. |
| `dwarf_only` / `debug_format` / `enable_debuginfod` / `debuginfod_url` | Debug-info resolution knobs. |
| `follow_dependencies` / `dependency_search_paths` | Dependency-closure walk. |
| `has_sources` | Legacy flag consulted by the `android` frontend's source-evidence rule. |

The exhaustive, generated field/type/default table for `DumpRequest` (and
every other typed request/result dataclass) lives in the [Python API
Reference](../reference/python-api-reference.md); the table above is a
curated subset for the fields most callers actually reach for.

`InputSpec.headers` combines fine with `sources`/`build_info` — that's the
normal way to collect additive L2 (headers) plus L3/L4 (build/source)
evidence in one request. Only `dump_manifest` is mutually exclusive with
`headers`/`includes`/`public_header_dirs` — a request combining those fails
validation before any extraction runs (`DumpRequest.validation_errors()`),
since a manifest already declares the equivalent surface itself.

### `CompareRequest`

```python
from pathlib import Path
from abicheck.api_types import CompareRequest, InputSpec
from abicheck.service import run_compare_request
from abicheck.service import resolve_compare_request, classify_compare_pair

request = CompareRequest(
    old=InputSpec(path=Path("libfoo.so.1")),
    new=InputSpec(path=Path("libfoo.so.2")),
    contract_evaluation=True,
    contract_mode="public",
)

# One call, the normal case:
result = run_compare_request(request)          # -> CompareResult

# Or the same thing in two steps, e.g. to inspect the resolved snapshots
# before classifying:
pair = resolve_compare_request(request)        # -> ResolvedComparePair (old/new snapshots)
result = classify_compare_pair(request, pair)   # -> CompareResult
```

`run_compare_request(request)` does both steps in one call — the two-step
form exists because the native CLI runs its own Click-specific resolution
(`--pack` application, receipt recording) *between* them; a typed caller
normally just wants `run_compare_request`.

### `ScanRequest`

Scan never had an untyped convenience shim — `ScanRequest` is the only way
in from Python:

```python
from pathlib import Path
from abicheck.service import ScanRequest, run_scan

result = run_scan(ScanRequest(
    binaries=[Path("build/libfoo.so")],
    baseline=Path("baseline.json"),
    depth="headers",
    contract_evaluation=True,
    contract_mode="exports",
))
```

## CLI / Python parity

The **rules** are shared across both front ends; the **surface** doesn't
always match field-for-field — `dump` still runs its own argument path
rather than building a `DumpRequest` end to end (see above). Read this
table as "where the capability is reachable today":

| Capability | CLI | Python (typed) |
|---|---|---|
| Depth floor | `dump --depth` → `DumpDepthNotSatisfiedError` | `DumpRequest.depth`/`CompareRequest.depth` → `ValidationError` |
| Not comparable | exit code `16` | raises `ProfileMismatchError`/`ScopeMismatchError` |
| Contract evaluation | `--contract {public,exports,all,auto}` | `CompareRequest.contract_evaluation`/`.contract_mode` (same fields on `ScanRequest`; the typed API still needs both, and has no `auto`) |
| Consumer scoping | `compare --used-by` | `abicheck.appcompat.scope_diff_to_app(...)` — no `CompareRequest` field, a post-classification step |

One asymmetry worth knowing about, not a bug to work around:

- **Consumer scoping has no `CompareRequest` field.** `--used-by` is a
  *post-classification* scoping pass layered on top of an already-computed
  `CompareResult` (`appcompat.scope_diff_to_app`), not a resolution input —
  the CLI and a direct Python caller both call the same function afterward,
  rather than a field on the request itself.

## Result types

- **`DiffResult`** (`abicheck.checker_types`) — the comparison result. Key
  fields: `verdict` (a `Verdict`), `changes` (`list[Change]`), and
  `suppressed_changes` (the suppression audit trail).
- **`Verdict`** (`abicheck.change_registry_types`) — one of `NO_CHANGE`,
  `COMPATIBLE`, `COMPATIBLE_WITH_RISK`, `API_BREAK`, `BREAKING`. See
  [Verdicts](../learn/verdicts.md) and, for the CLI mapping,
  [Exit Codes](../reference/exit-codes.md).
- **`AbiSnapshot`** (`abicheck.model`) — the serialisable ABI surface produced
  by `resolve_input` / `run_dump`.

The complete list of exported names, with full signatures/dataclass fields, is
the generated [Python API Reference](../reference/python-api-reference.md).
Public types live in `model.py`, `checker_types.py`, and `checker_policy.py`;
treat changes to their surface as breaking changes to this API.
