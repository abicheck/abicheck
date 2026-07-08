# Python API

abicheck's functionality is available as a Python library through the
`abicheck.service` module. This is the **supported public entry point** — the
same Tier-2 service layer the CLI and the [MCP server](mcp-integration.md) call.
Front-ends should route through `service` rather than importing the internal
`abicheck.checker` core directly (ADR-037).

> **Install.** `pip install abicheck`. Native-binary header analysis also needs
> `castxml` and a C++ compiler; without them, binary-only mode still works. See
> [Getting Started](../getting-started.md).

## Compare two libraries

`run_compare` is the one-call entry point: it resolves both inputs to snapshots,
runs the comparison, and returns the classified result.

```python
from pathlib import Path
from abicheck.service import run_compare

result, old_snapshot, new_snapshot = run_compare(
    old_input=Path("libfoo.so.1"),
    new_input=Path("libfoo.so.2"),
    old_headers=[Path("include/v1/foo.h")],
    new_headers=[Path("include/v2/foo.h")],
)

print(result.verdict)       # Verdict.BREAKING, Verdict.COMPATIBLE, ...
print(len(result.changes))  # number of detected changes
for change in result.changes:
    print(change.kind, change.name)
```

`run_compare` returns a `tuple[DiffResult, AbiSnapshot, AbiSnapshot]`. It raises
`SnapshotError` if an input cannot be loaded and `ValidationError` for an
unrecognised input format (both from `abicheck.errors`).

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
| `frontend` | `str` | `"auto"` | Header AST frontend (`"auto"`, `"castxml"`, `"clang"`) |
| `policy` | `str` | `"strict_abi"` | Built-in policy profile (`strict_abi`, `sdk_vendor`, `plugin_abi`) |
| `policy_file_path` | `Path` | `None` | Custom YAML policy file |
| `suppress` | `Path` | `None` | Suppression file (YAML or ABICC format) |
| `scope_to_public_surface` | `bool` | `True` | Restrict findings to the public ABI surface |
| `enable_debuginfod` | `bool` | `False` | Resolve debug info via debuginfod |

For the exhaustive argument set (PDB paths, debug roots, forced public symbols,
pattern verdicts), build a `CompareRequest`/`InputSpec` directly and call
`run_compare_request` — see the docstrings in `abicheck/service.py`.

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

`compare_snapshots` accepts the same policy/suppression/scoping keywords as
`run_compare` and returns a `DiffResult`. Snapshots are serialised as
`.abi.json` (`schema_version` `8`); see
[Output Formats](output-formats.md) for the on-disk contract and
[Local Compare](local-compare.md) for the baseline workflow.

## Render results

`render_output` turns a `DiffResult` into any of the supported report formats,
so you can reuse abicheck's exact reporter output from your own code.

```python
from abicheck.service import render_output

report = render_output("sarif", result, old_snapshot, new_snapshot)
Path("report.sarif").write_text(report)
```

Supported `fmt` values: `"markdown"`, `"json"`, `"sarif"`, `"html"`, `"junit"`.
`render_output` raises `ValidationError` for an unrecognised format.

## Result types

- **`DiffResult`** (`abicheck.checker_types`) — the comparison result. Key
  fields: `verdict` (a `Verdict`), `changes` (`list[Change]`), and
  `suppressed_changes` (the suppression audit trail).
- **`Verdict`** (`abicheck.change_registry_types`) — one of `NO_CHANGE`,
  `COMPATIBLE`, `COMPATIBLE_WITH_RISK`, `API_BREAK`, `BREAKING`. See
  [Verdicts](../concepts/verdicts.md) and, for the CLI mapping,
  [Exit Codes](../reference/exit-codes.md).
- **`AbiSnapshot`** (`abicheck.model`) — the serialisable ABI surface produced
  by `resolve_input` / `run_dump`.

The complete list of exported names is `abicheck.service.__all__`. Public types
live in `model.py`, `checker_types.py`, and `checker_policy.py`; treat changes to
their surface as breaking changes to this API.
