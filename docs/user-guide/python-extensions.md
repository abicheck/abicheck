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
what the module is certified against.

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

A normal `compare` of two extension modules adds up to three deployment-risk
findings:

| ChangeKind | Fires when |
|---|---|
| `python_stable_abi_violation` | the new build gained a **non-stable** import — a private `_Py*`/`PyUnstable_*` symbol outside the Stable ABI, or a public `Py*` symbol never added to the Limited API (e.g. `PyUnicode_AsUTF8`) — outside the Limited API regardless of interpreter version |
| `python_abi3_dropped` | the module was an `abi3` build (loadable on every interpreter at/above its floor) but the new build is **version-specific** — it drops every other interpreter it used to support |
| `python_gil_abi_changed` | the module switched between the regular (GIL) and **free-threaded** (PEP 703, `Py_GIL_DISABLED`) CPython ABI — its SOABI tag gained or lost the `t` marker (`cpython-3XX` ↔ `cpython-3XXt`). The two builds target different, non-interchangeable interpreter ABIs |

All three are classified `COMPATIBLE_WITH_RISK`: whether the module actually
breaks depends on the *target interpreter*, not on the module's own consumers.

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

!!! note "Interpreter-*floor* drift is checked by `scan --abi3`, not `compare`"
    Proving that a raised interpreter floor drops a *supported* interpreter
    needs the module's declared `Py_LIMITED_API` floor — and a bare `.abi3.so`
    doesn't carry its minor. Comparing the minimum-imported-symbol version across
    two builds would false-positive (a `cp39-abi3` build adding a 3.5 symbol
    drops no 3.9+ user), so `compare` deliberately does **not** flag floor drift.
    Use `scan --abi3 <floor>` — where you supply the target floor — to catch
    stable symbols newer than it.

!!! note "Version-specific modules are not checked"
    A per-version module (`foo.cpython-311-…so`) legitimately uses private
    CPython API and is rebuilt for each interpreter — it makes no
    cross-interpreter promise. The stable-ABI checks apply **only** to `abi3`
    builds (an `.abi3.` suffix or a `Py_LIMITED_API` build), so a normal
    versioned extension never raises a false positive here.

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
