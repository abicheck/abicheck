# Python Extension Modules & the `abi3` Stable-ABI Contract

A CPython **extension module** — whether built with **Cython**, **pybind11**,
**nanobind**, or hand-written C — is an ordinary shared library
(`.so` / `.pyd` / `.dylib`). abicheck already reads its symbols, types, and
layout like any other binary. But an extension module has a compatibility
contract the export table cannot show you.

## Why exports are the wrong surface

An extension module exports almost nothing — essentially just its init
function, `PyInit_<mod>`. Comparing two versions by their *exports* therefore
tells you very little:

```console
$ abicheck compare foo-1.0.abi3.so foo-2.0.abi3.so
Verdict: COMPATIBLE      # judged from 1 exported symbol
```

The surface that actually decides whether the module loads is what it
**imports** from `libpython`: the CPython C-API symbols it calls. An
[`abi3` / *Limited API*](https://docs.python.org/3/c-api/stable.html) wheel
promises it uses only the **stable** subset of that API, so one binary runs on
every interpreter at or above its declared floor (`cp39-abi3` → CPython 3.9+).
If the module imports a symbol outside that stable set — or one newer than its
declared floor — it fails to import on an older interpreter:

```text
ImportError: /.../foo.abi3.so: undefined symbol: _PyObject_GC_New
```

…and the export-table view would still call it `COMPATIBLE`.

## What abicheck checks

abicheck recognises an extension module automatically (from its `PyInit_*`
export and `Py*` import surface, across Cython/pybind11/nanobind/C) and captures
the imported CPython C-API symbols plus whether the module is a stable-ABI
(`abi3`) build. Two things are then checked.

!!! note "Only *CPython-provided* `Py*` symbols are judged"
    Some third-party libraries follow the `Py` C-API naming convention
    (NumPy's `PyArray_*`, a companion `PyFoo_*` library). On Windows the audit
    uses the **provider DLL** — only `Py*` symbols imported from an actual CPython
    runtime DLL (`python3.dll`/`pythonXY.dll`) count, so a `numpy.dll` import is
    never mistaken for a Limited-API violation. (NumPy's C API is reached through
    a runtime capsule rather than direct symbol linkage, so on ELF/Mach-O — where
    the undefined-symbol table carries no per-symbol provider — those names don't
    appear as imports anyway.)

### 1. Audit a single module — `scan --abi3`

```console
$ abicheck scan --binary foo.abi3.so --abi3 3.9
…
  abi3_audit         ran           118 CPython import(s) audited against
                                   Py_LIMITED_API 3.9; 1 violation finding(s)

Cross-source findings (advisory)
  [warning] python_stable_abi_violation: 1
```

`scan --abi3 <floor>` classifies every imported CPython symbol against the
vendored, authoritative Stable-ABI set (all `[function.*]`/`[data.*]` entries
from CPython's `Misc/stable_abi.toml`) for the target `Py_LIMITED_API` floor:

- **private/internal symbols** — a `_Py*`/`PyUnstable_*` name *not* in the
  Stable-ABI set → a **violation** (the module reached outside the Limited API);
- **public `Py*` symbols not in the Stable-ABI set** (e.g. `PyUnicode_AsUTF8`,
  which is public but was never added to the Limited API) → **violation**; the
  vendored set is authoritative, so absence means the symbol is not `abi3`. The
  one case that may be a false positive is a symbol *newer* than the vendored
  CPython release — it is flagged but the data should be refreshed to confirm;
- **stable symbols newer than the floor** (e.g. `PyType_GetName`, stable since
  3.11, under a `--abi3 3.9` target) → **violation**.

!!! note "`_Py`-prefix does not mean private"
    The Limited-API headers route public macros to underscore-prefixed
    `abi_only` symbols — `Py_DECREF` → `_Py_Dealloc`, `PyObject_GC_New` →
    `_PyObject_GC_New`, `PyArg_ParseTuple` (with `PY_SSIZE_T_CLEAN`) →
    `_PyArg_ParseTuple_SizeT`, `Py_None` → `&_Py_NoneStruct`. abicheck decides
    by **membership** in the vendored set, not by the name prefix, so these
    clean Limited-API imports are correctly classified as stable.

The `--binary` must be a CPython extension module (or a saved snapshot of one);
`--abi3` on a plain library is a usage error. The floor is **required** — it is
the target `Py_LIMITED_API` version you supply, so there is no ambiguity about
what the module is certified against. If the artifact's own SOABI tag says it is
**version-specific** (`foo.cpython-311-…so`, or a free-threaded `cpython-313t`),
that is flagged too: the tag pins it to one interpreter, so it cannot satisfy an
`abi3` floor no matter how stable its imports are — pointing `--abi3` at such a
build is a contradiction the audit surfaces rather than silently certifies.

!!! note "A lower declared `cpXY-abi3` tag floor wins over a higher `--abi3`"
    If the artifact's own tag declares a floor *below* the one you pass
    (`foo.cp39-abi3-…pyd` audited with `--abi3 3.12`), the audit certifies
    against the **declared** floor (3.9), not the supplied one. The tag is a hard
    contract a package manager honors — a `cp39-abi3` wheel is installed on
    3.9/3.10 regardless of your `--abi3` — so a symbol like `PyType_GetName`
    (stable only since 3.11) is a violation even under `--abi3 3.12`, because it
    is missing on the 3.9 the tag still advertises. The finding names the floor
    it used so the lowering is explicit.

**Gating.** Like every single-artifact `scan` check, stable-ABI violations are
**advisory by default** (they appear in the report but do not fail the scan) —
"adoption never starts by blocking merges". To gate CI on them, promote the
finding to an error:

```console
$ abicheck scan --binary foo.abi3.so --abi3 3.9 \
      --crosscheck python_stable_abi_violation=error
```

Then a violation raises the exit code to the source-break tier (`2`), failing
the build. Exit `0` = clean or advisory-only; a usage error (bad `--abi3`, or
`--abi3` on a non-extension) exits non-zero.

### 2. Compare two versions — `compare`

A normal `compare` of two extension modules adds up to four deployment-risk
findings:

| ChangeKind | Fires when |
|---|---|
| `python_stable_abi_violation` | the new build gained a **non-stable** import — a private `_Py*`/`PyUnstable_*` symbol outside the Stable ABI, or a public `Py*` symbol never added to the Limited API (e.g. `PyUnicode_AsUTF8`) — outside the Limited API regardless of interpreter version |
| `python_abi3_dropped` | the module was an `abi3` build (loadable on every interpreter at/above its floor) but the new build is **version-specific** — it drops every other interpreter it used to support |
| `python_gil_abi_changed` | the module switched between the regular (GIL) and **free-threaded** (PEP 703, `Py_GIL_DISABLED`) CPython ABI — its SOABI tag gained or lost the `t` marker (`cpython-3XX` ↔ `cpython-3XXt`). The two builds target different, non-interchangeable interpreter ABIs |
| `python_abi3_floor_raised` | both builds are `abi3` and carry an explicit `cpXY-abi3` tag, but the new build's **declared floor is higher** (`cp39-abi3` → `cp310-abi3`) — dropping every interpreter in the abandoned range. Exact (read from the tag on both sides), so no min-of-imports guessing |

All four are classified `COMPATIBLE_WITH_RISK`: whether the module actually
breaks depends on the *target interpreter*, not on the module's own consumers.

!!! note "How these gate CI"
    These four are **compare-time** kinds. Like every `RISK` change they are
    advisory in `compare` by default; gate them through `compare`'s severity /
    policy configuration (e.g. a policy profile that escalates the kind, or the
    `--severity-*` flags). The `--crosscheck python_stable_abi_violation=error`
    switch is specific to the single-artifact **`scan --abi3` audit** — it does
    not gate the compare-time kinds, which ride the `compare` verdict instead.

!!! note "Free-threaded (no-GIL) builds are never `abi3`"
    A free-threaded build (PEP 703, `cpython-313t` / `cp314t`) uses a different
    CPython ABI than the regular GIL build of the same minor, and
    `Py_LIMITED_API` is **incompatible** with `Py_GIL_DISABLED` — so a
    free-threaded wheel is always version-specific, never `abi3`. abicheck
    recognises the `t` marker, treats such a module as version-specific (so the
    stable-ABI contract correctly does not apply), and flags a
    `python_gil_abi_changed` risk when a module crosses the GIL/no-GIL boundary
    between builds.

!!! note "Windows `abi3` must link `python3.dll`, not `pythonXY.dll`"
    On Windows the Stable ABI links against the version-neutral `python3.dll`
    forwarder; a version-specific `python311.dll` ties the module to one
    interpreter minor and it will not load on another — no matter how stable its
    imported *symbol names* are. abicheck reads the PE import table's provider
    DLL, so an `abi3` `.pyd` that links a `pythonXY.dll` is flagged as a
    `python_stable_abi_violation` (in both `scan --abi3` and `compare`), even
    when every imported symbol is in the stable set.

!!! note "Floor drift: exact when declared, otherwise deferred to `scan --abi3`"
    `compare` flags `python_abi3_floor_raised` only from the **explicit
    `cpXY-abi3` tag** on *both* builds (e.g. `cp39-abi3` → `cp310-abi3`) — that is
    exact. It deliberately does **not** *infer* a floor from the imported-symbol
    versions: a bare `.abi3.so` carries no declared minor, and the min-of-imports
    heuristic false-positives (a `cp39-abi3` build adding a 3.5 symbol drops no
    3.9+ user). When the floor isn't declared in the tag, use
    `scan --abi3 <floor>` — where you supply the target floor — to catch stable
    symbols newer than it.

!!! note "Version-specific modules are not checked"
    A per-version module (`foo.cpython-311-…so`) legitimately uses private
    CPython API and is rebuilt for each interpreter — it makes no
    cross-interpreter promise. The stable-ABI checks apply **only** to `abi3`
    builds (an `.abi3.` suffix or a `Py_LIMITED_API` build), so a normal
    versioned extension never raises a false positive here.

## Beyond the C-ABI: the Python-level API

The `abi3` checks above decide whether the compiled `.so`/`.pyd` **loads**. But
that is not the contract most consumers depend on — what they `import` is the
module's **Python-level API**: its top-level functions, classes, methods, and
their signatures. Two builds can be byte-for-byte C-ABI-identical yet break
every caller:

```python
# v1
def transform(data, *, encoding="utf-8"): ...
# v2 — same PyInit_, same imported Py* surface, same abi3 tag…
def transform(data, codec): ...   # renamed kwarg, dropped default
```

The export table is still one `PyInit_` symbol and the imported C-API is
unchanged, so `compare`/`scan --abi3` see nothing. The break lives entirely in
the Python signatures, which are not in the binary's ABI surface at all.

### Where the surface comes from

There are no clean public C headers to lean on (Cython emits internal CPython
API; pybind11/nanobind headers describe the framework, not your module). So the
Python surface is recovered from a **PEP 484 type stub** (`.pyi`) shipped
alongside the binary — the richest, safest source, and the analog of C-header
diffing for Python. It is parsed **statically** (via `ast`), never imported or
executed. abicheck looks for the stub next to the artifact automatically:

```text
mypkg/
  foo.cpython-311-x86_64-linux-gnu.so
  foo.pyi          ← recovered as foo's Python-level API surface
```

(also `foo-stubs/__init__.pyi` and `foo/__init__.pyi`). When no stub is found
the check degrades honestly — it reports nothing rather than false-negating.
Both `abicheck dump` and `abicheck compare` attach and diff the surface
automatically; a single `compare` surfaces **both** the native-ABI changes and
the Python-API changes.

### A worked example

Two builds of the same extension. `v1` ships this stub:

```python
# mymod.pyi (v1)
def transform(data, *, encoding="utf-8") -> bytes: ...
```

`v2` renames the keyword argument and drops its default — the compiled `.so` is
otherwise unchanged (same `PyInit_mymod`, same imported `Py*` surface):

```python
# mymod.pyi (v2)
def transform(data, codec) -> bytes: ...
```

Dump each build (the dumper reads the sibling `.pyi` automatically) and compare:

```console
$ abicheck dump mymod-1.0.cpython-311-x86_64-linux-gnu.so -o v1.abi.json
$ abicheck dump mymod-2.0.cpython-311-x86_64-linux-gnu.so -o v2.abi.json
$ abicheck compare v1.abi.json v2.abi.json
| **Verdict** | ⚠️ `API_BREAK` |
...
## ⚠️ Source-Level Breaks
- **python_api_parameter_renamed**: Python parameter renamed in transform: encoding → codec
  > Callers that passed it by keyword hit an unexpected-keyword TypeError. The
  > compiled binary is byte-identical — this is the canonical break the
  > native-ABI check misses.
```

Exit code `2` (source-level break). If those two builds had *also* churned an
internal C++ symbol or struct, that native change would be demoted off-surface
(see [below](#the-python-api-as-a-false-positive-filter)) and would **not**
appear here — only the real Python-level break drives the verdict.

### What it detects

| ChangeKind | Verdict | Fires when |
|---|---|---|
| `python_api_function_removed` | `API_BREAK` | a public top-level function disappeared |
| `python_api_class_removed` | `API_BREAK` | a public class disappeared |
| `python_api_method_removed` | `API_BREAK` | a public method was removed from a class that still exists |
| `python_api_parameter_removed` | `API_BREAK` | a parameter was dropped — callers passing it hit a `TypeError` |
| `python_api_parameter_added` | `API_BREAK` | a new **required** (no-default) parameter was added — every existing call now raises a missing-argument `TypeError` |
| `python_api_parameter_renamed` | `API_BREAK` | a parameter was renamed — keyword callers hit an unexpected-keyword `TypeError` |
| `python_api_default_removed` | `API_BREAK` | a parameter lost its default, making a previously optional argument mandatory |
| `python_api_parameter_kind_changed` | `API_BREAK` | a parameter's **binding** changed with its name unchanged — positional↔keyword-only, keyword→positional-only, or the positional order/position shifted (a reorder, or an optional parameter inserted mid-list) — so existing positional or keyword callers bind arguments differently |
| `python_api_callable_kind_changed` | `API_BREAK` | a callable's **protocol** changed with an unchanged parameter list — `def`↔`async def` (callers must/mustn't `await`), or a class member changed between instance method, `@staticmethod`, `@classmethod`, and `@property` (call vs attribute access, different bind) |
| `python_api_overload_removed` | `API_BREAK` | an `@overload` signature variant was dropped from an overloaded function/method — typed callers relying on that call shape lose it |
| `python_api_stub_invalid` | `API_BREAK` | a shipped stub was unreadable, oversized, or syntactically invalid, so Python API coverage cannot be trusted |
| `python_api_parameter_type_changed` | `RISK` | a parameter's type annotation changed |
| `python_api_return_type_changed` | `RISK` | a function/method's return annotation changed |
| `python_api_function_added` / `python_api_class_added` / `python_api_method_added` | `COMPATIBLE` | new public surface — existing callers unaffected |

Removals, renames, added-required parameters, and dropped defaults are
**source-level breaks** (`API_BREAK`): a Python module has no separate binary
layer here, so a signature change never corrupts an already-loaded caller — it
breaks re-import / re-call at the source. Annotation changes are a
type-checker / behavioural **`RISK`**. Additions are `COMPATIBLE`. Adding an
*optional* parameter, adding a default, or adding an annotation is backward
compatible and is **not** reported. The signature diff is **order- and
kind-aware** — it compares the ordered parameter list and each parameter's kind,
not just the set of names — so a call-shape break with unchanged names (a
positional argument made keyword-only, a reorder, or an optional parameter
inserted before an existing one) is caught rather than silently missed. It also
captures each callable's **protocol** — `async` vs sync, and the descriptor kind
(instance/`static`/`class`/`property`) — and every `@overload` variant, so a
`def`→`async def` flip, a method↔property change, or a dropped overload is
reported even when the parameter list is untouched.

### The Python API as a false-positive filter

The recovered surface is not just a source of new findings — it is also an
**authoritative public-contract oracle** that removes native false positives. An
extension module exports only `PyInit_<mod>`; its other exported C/C++ symbols
and internal type layout (the Cython/pybind11 machinery, inline thunks, helper
classes) cannot be linked or observed by any `import` consumer. So when you dump
an extension with debug info (`-g`) or otherwise surface that internal churn, a
native detector calling it *breaking* is a false positive for the module's real
consumers.

When a `.pyi` surface was recovered, abicheck uses it as the public-contract
oracle: native C/C++ **API-content** findings (symbol removals, struct/enum
layout changes, vtable/RTTI churn) on anything but the module init export are
demoted to the audit ledger (`out_of_surface`, disclosed with reason
`off-python-surface`) instead of driving the verdict. Three guarantees keep this
safe:

- **Authority rule** — `python_api_*` findings and the native **load-contract**
  findings (`python_stable_abi_violation`, `python_abi3_dropped`,
  `python_gil_abi_changed`, `python_abi3_floor_raised`) are *never* demoted, so
  a real Python-level or load break is never hidden.
- **Load/linkage kept** — `needed_*`, `soname_*`, symbol-version, and security
  findings affect whether the `.so` loads, so they stay in the verdict.
- **Headers win** — if you *do* supply public C headers (a hybrid module with a
  real C API), the header-scoped surface is authoritative and this oracle defers
  to it; and with no recovered surface the check degrades honestly (nothing is
  demoted). It rides the same `--scope-public-headers` switch as the rest of
  surface scoping (on by default).

This is measured as a first-class evidence layer: the FP-rate gate carries a
`python-api` axis (internal native churn must scope away; a Python-API break
must stay breaking), and the per-tier accuracy gate records the Python surface
as an L2-only signal that L0/L1 honestly under-call.

!!! note "`self` / `cls` and private names"
    The leading `self` / `cls` of an instance/class method is dropped before
    diffing (it is bound by the descriptor protocol, not passed by callers), so
    renaming it is never a finding; a `@staticmethod` keeps all its parameters.
    Private names (leading underscore) are excluded from the surface, but dunder
    methods like `__init__` and `__call__` are kept — they are part of the
    callable contract.

## The Stable-ABI dataset

Classification is driven by a **vendored, authoritative** copy of CPython's
[`Misc/stable_abi.toml`](https://github.com/python/cpython/blob/main/Misc/stable_abi.toml)
— every linkable `[function.*]`/`[data.*]` entry (≈970 symbols) mapped to the
release it entered the Limited API, including the `abi_only` `_Py*` symbols the
public macros expand to. Membership in this set — not a name prefix — decides
whether an import is stable, so `_Py`-prefixed `abi_only` symbols are handled
correctly and a genuinely-internal `_Py*` import is a real violation.

The dataset is refreshable: re-run the extraction over a newer
`Misc/stable_abi.toml` (see `abicheck/stable_abi_data.py`). A public `Py*`
symbol not in the vendored set is a **violation** — the vendored set is
authoritative, so absence means the symbol is not `abi3`. The sole benign case
is a symbol from a CPython release *newer* than the vendored data: the audit
still flags it, but notes to refresh the dataset to confirm, so lag against a
brand-new CPython release surfaces as a flagged import to double-check rather
than a silent pass.
