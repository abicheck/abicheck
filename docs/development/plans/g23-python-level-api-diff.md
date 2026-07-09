# G23 — Python-level API diff for extension modules

**Registry:** `UC-ARCH-python-api` (`complete`)
**Effort:** XL · **Risk:** medium
**Status:** Done (static `.pyi` path). Follow-up to [G14](g14-stable-abi-subset.md).
Implemented in `abicheck/python_api.py` (surface + `ast` stub extractor) and
`abicheck/diff_python_api.py` (detector); 12 `python_api_*` `ChangeKind`s; tests
in `tests/test_python_api.py`; user docs in
[python-extensions.md](../../user-guide/python-extensions.md#beyond-the-c-abi-the-python-level-api).
The opt-in runtime-introspection / docstring fallbacks and an `examples/`
catalog fixture remain future work (see *Out of scope* / *Tests*).

## Problem

G14 checks the **native C-ABI** contract of a CPython extension module — the
`Py*` symbols it imports from libpython and its `abi3` conformance. That is the
contract that decides whether the compiled `.so`/`.pyd` *loads*. But it is not
the contract most consumers actually depend on: the **Python-level API** the
module exposes to `import` — its functions, classes, methods, their argument
names/types/defaults, and return types.

Two builds can be C-ABI-identical yet break every caller:

```python
# v1
def transform(data, *, encoding="utf-8"): ...
# v2 — same PyInit_, same imported Py* surface, same abi3 tag…
def transform(data, codec):   # renamed kwarg, dropped default → every caller breaks
    ...
```

`abicheck compare`/`scan --abi3` see nothing here: the export table is still one
`PyInit_` symbol and the imported C-API is unchanged. The break lives in the
Python signatures, which are not in the binary's ABI surface at all.

**There are no clean public C headers to lean on.** Cython emits a `.c` full of
*internal* CPython API; pybind11/nanobind are header-only C++ template libraries
whose headers describe the framework, not the module's API. So abicheck's
existing castxml/clang header path (L2) does not apply to extension modules. The
Python-level surface must be recovered from Python-world artifacts.

## Goal & acceptance criteria

- [x] Extract an extension module's **Python-level API surface** — top-level
      functions, classes, methods, and their signatures (parameter
      names, kinds — positional/keyword-only/var — defaults, and type
      annotations where available). *(Attributes deferred — no `ChangeKind`
      covers them yet.)*
- [x] Diff two surfaces and emit Python-level `ChangeKind`s:
      `python_api_function_removed`, `python_api_parameter_removed`,
      `python_api_parameter_renamed`, `python_api_default_removed`,
      `python_api_return_type_changed`, `python_api_class_removed`,
      `python_api_method_removed` (plus `*_added`, `python_api_parameter_added`,
      `python_api_parameter_type_changed`) — each classified `API_BREAK` /
      `RISK` / `COMPATIBLE` per the root `CLAUDE.md` four-step procedure.
- [x] Works from a **static** source (no import/execution of the module) —
      `.pyi` stubs parsed with `ast`. A runtime-introspection fallback remains
      optional/opt-in future work.
- [x] Complements, does not replace, the G14 C-ABI check: a single `compare`
      surfaces both native-ABI and Python-API changes.

## Design

Surface sources, cheapest/safest first:

1. **`.pyi` type stubs** (PEP 484) shipped in the wheel — the richest and safest
   source: full signatures with annotations, statically parseable with `ast`.
   This is the primary path and the analog of C-header diffing for Python.
2. **Embedded signatures** — pybind11/nanobind bake `def foo(x: int) -> str`
   into `__doc__`/`__text_signature__`; Cython carries them in `.pxd` and
   docstrings. Parse without importing.
3. **Runtime introspection** (opt-in, sandboxed) — `import` the module and walk
   `inspect.signature` / `__all__`. Most accurate but requires executing the
   module and a matching interpreter, so it is a deliberate, flagged fallback
   (never the default; see the MCP/security posture in ADR-021b).

A new `abicheck/python_api.py` builds a `PythonApiSurface` (attached to
`AbiSnapshot` like `python_ext`), a `diff_python_api.py` diffs two surfaces, and
the new `ChangeKind`s route through the existing reporter and verdict machinery.
Recovery is uniform across builders because all three expose the same Python
objects; the *source* (stub vs docstring vs runtime) differs, not the model.

## Files & surfaces

- New `abicheck/python_api.py` (surface model + extractors: stub/`ast`,
  docstring, optional runtime), `abicheck/diff_python_api.py` (detector),
  `abicheck/checker_policy.py` + `abicheck/change_registry.py` (new kinds),
  `abicheck/model.py` (`python_api` field), `abicheck/serialization.py`
  (persist/derive), reuse of `abicheck/reporter.py`. Surfaced through `compare`
  and `scan` (no new top-level command — consistent with the G14 → `scan --abi3`
  consolidation).

## Tests

- Unit: stub/docstring pairs exercising each kind (removed function, renamed
  kwarg, dropped default, narrowed/added annotation, removed class/method).
- Round-trip serialization of `python_api`.
- An `examples/` pair with a `ground_truth.json` entry demonstrating a
  Python-API break that the C-ABI/`abi3` check alone scores `COMPATIBLE`.

## Example fixtures

A two-version extension whose compiled surface is byte-identical but whose `.pyi`
renames a keyword argument — ground truth: `python_api_parameter_renamed`
(`API_BREAK`), while the G14 checks stay clean.

## Effort & risk

XL — a new frontend (stub/docstring parsing), a family of new `ChangeKind`s, and
example fixtures. Medium risk: the static sources (`.pyi`, `__text_signature__`)
are well-specified, but coverage varies by builder (hand-written C extensions
often ship neither), so the check must degrade honestly (report what surface it
could recover, like the scan coverage rows) rather than false-negative silently.

## Out of scope

Behavioural/semantic changes (a function that keeps its signature but changes
what it returns); pure-Python packages (this targets *extension modules* — the
gap C-ABI tooling misses); non-CPython runtimes.
