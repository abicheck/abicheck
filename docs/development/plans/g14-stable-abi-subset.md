# G14 — CPython Limited-API / `abi3` import-contract conformance

**Registry:** `UC-WF-stable-abi-subset` (`planned`)
**Effort:** M · **Risk:** low

## Problem

Every `abi3` wheel promises it uses only the **stable** CPython C-API subset, so
one binary runs on all `Py_LIMITED_API`-compatible interpreters. abicheck has
the substrate to verify this (the ELF/PE/Mach-O **undefined-symbol** table, plus
the appcompat engine) but no driver that checks it. Today `compare`/`appcompat`
reason about a binary's **exports**; for an extension module the exports are
essentially just `PyInit_<mod>` — the compatibility surface that actually
matters is what the module **imports** from libpython.

Empirically confirmed (cryptography 42.0.8 → 43.0.3, both `cp39-abi3`):

- `abicheck compare` returns `COMPATIBLE` — judged entirely from the export
  table (1 → 25 exported symbols).
- The real contract is invisible: the module's imported CPython symbols grow
  from **111 → 118 `Py*`** (e.g. `PyByteArray_AsString/Size/Type`,
  `PyNumber_And/Multiply/Power`, `PyObject_GenericSetDict`, `Py_DecRef/IncRef`).
  If any imported symbol were outside the `abi3` set, or newer than the wheel's
  declared `Py_LIMITED_API` floor, the wheel would fail to import on an older
  interpreter with an `undefined symbol` error — and abicheck would still say
  `COMPATIBLE`.

## Goal & acceptance criteria

- [ ] A check that enumerates an extension's **imported** CPython C-API symbols
      and classifies each against a stable-ABI allowlist for a target
      `Py_LIMITED_API` version.
- [ ] An imported symbol outside the `abi3` set (or newer than the declared
      floor) is reported as a deployment-`RISK`/`BREAKING` finding that reaches
      the verdict and JSON/SARIF — not silently `COMPATIBLE`.
- [ ] A clean `abi3` extension passes; a `--no-limited-api` extension that
      imports a non-stable symbol is flagged.

## Design

1. Capture undefined/imported symbols already available in the snapshot
   substrate; expose an imported-symbol view on the model if not present.
2. Ship a stable-ABI allowlist (the `abi3` symbol set per CPython minor; sourced
   from CPython's `Doc/data/stable_abi.dat`, vendored as data, refreshable).
3. A driver `abicheck stable-abi <ext.so> [--abi3 3.9]` classifies imports;
   wires through the existing reporter and a new `RISK`/`BREAKING` `ChangeKind`
   (added per the root `CLAUDE.md` four-step procedure).
4. Cross-platform: the same idea applies to `python3.dll` (PE imports) and the
   macOS `.so` two-level-namespace imports.

## Files & surfaces

- `abicheck/model.py` (imported-symbol view if missing), a new
  `abicheck/stable_abi.py` + vendored allowlist data, a CLI module
  `abicheck/cli_stable_abi.py` (registered per the root `CLAUDE.md` "Adding a new
  top-level command"), `abicheck/checker_policy.py` (new kind), reuse of
  `abicheck/reporter.py`.

## Tests

- Unit: a synthetic extension importing only `abi3` symbols → pass; one importing
  a non-stable `_Py*`/version-newer symbol → flagged.
- An example pair under `examples/` with `ground_truth.json` entry.

## Out of scope

Verifying a wheel's *declared* tag against its contents end-to-end (packaging
concern); non-CPython stable ABIs.
