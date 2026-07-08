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
  CPython symbol: _PyObject_GC_New
```

`stable-abi` classifies every imported CPython symbol against the Limited-API
allowlist for the target `Py_LIMITED_API` floor:

- **private `_Py*` symbols** are never part of the stable ABI → **violation**;
- **stable symbols newer than the floor** (e.g. `PyType_GetName`, added 3.11,
  under a `--abi3 3.9` target) → **violation**;
- **public `Py*` symbols not in the curated allowlist** → reported as an
  *advisory* (they may be stable — the allowlist is a refreshable subset).

The floor comes from `--abi3`, or from the module's own SOABI tag when omitted.

Exit codes: `0` = clean, `1` = one or more violations, `2` = the input is not a
recognisable extension module. Wire it into CI to gate a wheel before you ship
it.

### 2. Compare two versions — `compare`

A normal `compare` of two extension modules adds two deployment-risk findings
for stable-ABI (`abi3`) builds:

| ChangeKind | Fires when |
|---|---|
| `python_stable_abi_violation` | the new build gained a private `_Py*` import that the old build did not have |
| `python_abi_floor_raised` | the new build's imports require a **newer** minimum interpreter than the old build (e.g. it now calls a 3.11-only Limited-API symbol) — so it drops the older interpreters it used to load on |

Both are classified `COMPATIBLE_WITH_RISK`: whether the module actually breaks
depends on the *target interpreter*, not on the module's own consumers.

!!! note "Version-specific modules are not checked"
    A per-version module (`foo.cpython-311-…so`) legitimately uses private
    CPython API and is rebuilt for each interpreter — it makes no
    cross-interpreter promise. The stable-ABI checks apply **only** to `abi3`
    builds (an `.abi3.` suffix or a `Py_LIMITED_API` build), so a normal
    versioned extension never raises a false positive here.

## The allowlist

The stable-ABI allowlist is a curated, refreshable subset of CPython's
canonical [`Doc/data/stable_abi.dat`](https://github.com/python/cpython/blob/main/Doc/data/stable_abi.dat),
mapping each recognised symbol to the release that added it to the Limited API.
The always-correct signal — a private `_Py*` import — needs no allowlist and
never goes stale; the allowlist only refines the *floor* computation. A public
symbol not in the subset is reported as advisory, never as a hard break, so
allowlist lag can never cause a false positive.
