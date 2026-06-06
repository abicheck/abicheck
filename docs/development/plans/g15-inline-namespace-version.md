# G15 — inline-namespace version-stamp normalization

**Registry:** `UC-CHANGE-inline-ns-version` (`planned`)
**Effort:** M · **Risk:** medium

## Problem

Some C/C++ libraries stamp **every** exported symbol with a per-release version
token — an inline namespace or a renaming macro — so that mixing two releases
link-fails by construction. ICU is the canonical case (`icu_73` → `icu_74`); the
same shape appears in libstdc++ versioned namespaces and Abseil's
`absl::lts_YYYYMMDD`. Comparing two such releases makes **every** symbol look
removed + re-added, burying the real, usually-additive delta.

Empirically confirmed (ICU `libicuuc.so.73.2` → `libicuuc.so.74.2`):

- `abicheck compare` returns **`BREAKING` with 6288 changes**.
- Raw-name intersection of the `_73`/`_74`-stamped symbols: **0** — total
  phantom churn.
- After normalizing the version token: **1074 / 1074** old symbols still present,
  **0 real removals**, **34 genuine additions**. The truthful verdict is
  *additive* (plus a deliberate soname bump), not a 6288-change break.

This is the same false-noise topology as G9 (auditwheel hashed sonames) but at
the **C++ symbol** level rather than the filename level.

## Goal & acceptance criteria

- [ ] A normalization pass strips a recognised version token from symbol names
      before diffing, so ICU 73→74 reports "additive, 34 new symbols, soname
      bumped" instead of 6288 phantom changes.
- [ ] The deliberate soname bump is still surfaced as the real "you must relink"
      signal (normalization must not hide it).
- [ ] A genuinely removed/changed symbol (beyond the version token) is still
      detected.
- [ ] Opt-in / auto-detected so ordinary libraries are unaffected (false-positive
      guard).

## Design

1. Detect the stamp from a high-coverage suffix/namespace pattern: a token
   shared by a large majority of exported symbols (`_<NN>` suffix for ICU,
   `__<N>` inline-namespace mangling, `lts_<date>`), confirmed against the
   library `SONAME` version so unrelated names are not stripped.
2. Normalize both snapshots' symbol keys through the detected token, diff the
   normalized surface, then re-annotate findings with the original names.
3. Emit a single informational "inline-namespace version advanced `73`→`74`"
   note carrying the soname-bump signal, rather than per-symbol churn.

## Risk

Over-eager stripping could mask a real rename. Mitigated by (a) requiring the
token to cover a strong majority of symbols and (b) cross-checking the SONAME —
both conditions must hold before normalization engages.

## Files & surfaces

- A normalizer in `abicheck/demangle.py` / `abicheck/classify.py`, hooked into
  `abicheck/diff_symbols.py`; a detection helper; opt-in flag on the relevant
  CLI module; possibly a new informational `ChangeKind`.

## Tests

- Unit: ICU-shaped symbol sets → additive verdict + soname-bump note; a control
  library with an incidental `_NN` suffix → **not** normalized.
- An example pair under `examples/` with `ground_truth.json` entry.

## Out of scope

Demangling-level inline-namespace collapse for arbitrary C++ ABIs beyond the
detected stamp pattern.
