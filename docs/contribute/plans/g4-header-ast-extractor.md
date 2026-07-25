# G4 — libclang header-AST extractor (header-only / inline-only frontier)

**Registry:** `UC-ARCH-header-only` (`planned`)
**Effort:** XL · **Risk:** high (new heavy dependency, parser parity)

## Problem

The castxml dump path (`abicheck/dumper_castxml.py`) cannot observe several
source-level constructs, which leaves header-only and inline-heavy libraries
under-analysed. Concretely, these example fixtures are preserved but currently
return `NO_CHANGE` end-to-end:

- **case105** — C++20 concept tightening (castxml emits `<Unimplemented
  kind="Concept"/>` with no body).
- **case106** — conversion operator gaining `explicit` (no `explicit` on
  `<Converter>`).
- **case78 / case111** — user-declared constructors have no mangled name in
  castxml, so ctor add/remove is invisible.

This is the single highest-leverage *detection* investment: one extractor
unblocks ~4 dormant fixtures and header-only coverage generally.

## Goal & acceptance criteria

- [ ] An optional libclang-based extractor produces, per public declaration:
      concept names + `requires`-expression text, `explicit` on constructors and
      conversion operators, and mangled names for user-declared constructors.
- [ ] cases 78/105/106/111 reach their intended verdict end-to-end and their
      `known_gap` notes are removed from `ground_truth.json`.
- [ ] The extractor is **opt-in** and degrades gracefully (castxml remains the
      default; absence of libclang is a warning, not an error) — preserving the
      "lightweight, pure-Python core" non-goal posture.

## Design

1. New module `abicheck/dumper_libclang.py` using the `clang.cindex` Python
   bindings (declared as an optional extra, e.g. `abicheck[clang]`), mirroring
   the `AbiSnapshot` shapes produced by `dumper_castxml.py` so the diff stage is
   source-agnostic.
2. Capture into `model.py`: concept constraints (new field on a Concept record),
   `explicit` flags (already partly modelled — see `CTOR_EXPLICIT_ADDED`),
   ctor mangled names (Itanium mangling via libclang).
3. Selection: a `--header-ast {castxml,libclang,auto}` option on `dump`/`compare`;
   `auto` prefers libclang when available for the constructs castxml misses,
   else falls back.
4. Wire detectors: concept tightening → a new `CONCEPT_CONSTRAINT_TIGHTENED`
   ChangeKind (follow the four-step ChangeKind procedure in `/CLAUDE.md`);
   reuse `CTOR_EXPLICIT_ADDED`, `HIDDEN_FRIEND_REMOVED`, `FUNC_ADDED/REMOVED`.

## Files & surfaces

- `abicheck/dumper_libclang.py` (new), `abicheck/model.py` (concept fields),
  `abicheck/checker_policy.py` + `change_registry.py` (new ChangeKind),
  `abicheck/dwarf_unified.py`/dump routing, `pyproject.toml` (optional extra).

## Tests

- Unit: extractor over fixture headers (mocked/real libclang), `@pytest.mark.integration`.
- Promote cases 78/105/106/111 in `tests/test_abi_examples.py`.

## Out of scope

Replacing castxml (it stays the default). Full template-instantiation reasoning
beyond what the snapshots already model.
