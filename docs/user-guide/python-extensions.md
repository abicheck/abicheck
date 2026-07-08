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

### 1. Audit a single module — `stable-abi`

```console
$ abicheck stable-abi foo.abi3.so --abi3 3.9
stable-abi: foo — 118 CPython import(s), target abi3 floor 3.9, 1 finding(s)…

## ⚠️ Deployment Risk Changes
- python_stable_abi_violation: abi3 extension 'foo' imports non-stable
  CPython symbol: _PyObject_LookupSpecial
```

`stable-abi` classifies every imported CPython symbol against the vendored,
authoritative Stable-ABI set (all `[function.*]`/`[data.*]` entries from
CPython's `Misc/stable_abi.toml`) for the target `Py_LIMITED_API` floor:

- **private/internal symbols** — a `_Py*`/`PyUnstable_*` name *not* in the
  Stable-ABI set → a **violation** (the module reached outside the Limited API);
- **public `Py*` symbols not in the Stable-ABI set** (e.g. `PyUnicode_AsUTF8`,
  which is public but was never added to the Limited API) → **violation**; the
  vendored set is authoritative, so absence means the symbol is not `abi3`. The
  one benign case is a symbol *newer* than the vendored CPython release — the
  CLI flags it but notes to refresh the data to confirm;
- **stable symbols newer than the floor** (e.g. `PyType_GetName`, stable since
  3.11, under a `--abi3 3.9` target) → **violation**.

!!! note "`_Py`-prefix does not mean private"
    The Limited-API headers route public macros to underscore-prefixed
    `abi_only` symbols — `Py_DECREF` → `_Py_Dealloc`, `PyObject_GC_New` →
    `_PyObject_GC_New`, `PyArg_ParseTuple` (with `PY_SSIZE_T_CLEAN`) →
    `_PyArg_ParseTuple_SizeT`, `Py_None` → `&_Py_NoneStruct`. abicheck decides
    by **membership** in the vendored set, not by the name prefix, so these
    clean Limited-API imports are correctly classified as stable.

The floor comes from `--abi3`, or from the module's own SOABI tag when omitted.

Exit codes: `0` = clean, `1` = one or more violations, `2` = the input is not a
recognisable extension module, `3` = **incomplete** — an `abi3` module was given
without a resolvable target floor, so the stable-symbol floor check could not run
and the module cannot be certified (pass `--abi3 <floor>`). Wire it into CI to
gate a wheel before you ship it; both `1` and `3` fail the gate.

### 2. Compare two versions — `compare`

A normal `compare` of two extension modules adds one deployment-risk finding for
stable-ABI (`abi3`) builds:

| ChangeKind | Fires when |
|---|---|
| `python_stable_abi_violation` | the new build gained a **private/unstable** import (a `_Py*` symbol outside the Stable ABI, or a `PyUnstable_*` symbol) — outside the Limited API regardless of interpreter version |
| `python_abi3_dropped` | the module was an `abi3` build (loadable on every interpreter at/above its floor) but the new build is **version-specific** — it drops every other interpreter it used to support |

Both are classified `COMPATIBLE_WITH_RISK`: whether the module actually breaks
depends on the *target interpreter*, not on the module's own consumers.

!!! note "Interpreter-*floor* drift is checked by `stable-abi`, not `compare`"
    Proving that a raised interpreter floor drops a *supported* interpreter
    needs the module's declared `Py_LIMITED_API` floor — and a bare `.abi3.so`
    doesn't carry its minor. Comparing the minimum-imported-symbol version across
    two builds would false-positive (a `cp39-abi3` build adding a 3.5 symbol
    drops no 3.9+ user), so `compare` deliberately does **not** flag floor drift.
    Use `stable-abi --abi3 <floor>` — where you supply the target floor — to
    catch stable symbols newer than it.

!!! note "Version-specific modules are not checked"
    A per-version module (`foo.cpython-311-…so`) legitimately uses private
    CPython API and is rebuilt for each interpreter — it makes no
    cross-interpreter promise. The stable-ABI checks apply **only** to `abi3`
    builds (an `.abi3.` suffix or a `Py_LIMITED_API` build), so a normal
    versioned extension never raises a false positive here.

## The Stable-ABI dataset

Classification is driven by a **vendored, authoritative** copy of CPython's
[`Misc/stable_abi.toml`](https://github.com/python/cpython/blob/main/Misc/stable_abi.toml)
— every linkable `[function.*]`/`[data.*]` entry (≈900 symbols) mapped to the
release it entered the Limited API, including the `abi_only` `_Py*` symbols the
public macros expand to. Membership in this set — not a name prefix — decides
whether an import is stable, so `_Py`-prefixed `abi_only` symbols are handled
correctly and a genuinely-internal `_Py*` import is a real violation.

The dataset is refreshable: re-run the extraction over a newer
`Misc/stable_abi.toml` (see `abicheck/stable_abi_data.py`). A public `Py*`
symbol not in the vendored set is reported as advisory, never as a hard break,
so lag against a brand-new CPython release can never cause a false positive.
