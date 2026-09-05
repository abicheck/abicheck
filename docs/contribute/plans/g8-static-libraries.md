# G8 — Static-library (`.a` / `.lib`) stance

**Registry:** `UC-ARCH-static-lib` (`by_design_excluded`)
**Effort:** S (decision) → M (if implemented) · **Risk:** low

## Problem

Static/import library archives are unsupported input today. A user pointing
`abicheck` at a `.a`/`.lib` gets an explicit guidance error instead of a late or
misleading parse failure.

**Amendment (2026-09, clarification):** `vision.md`'s current, authoritative
scope statement is narrower than this document's "documented non-goal"
framing below: static archives "merit a bounded, lower-priority
investigation into which questions can honestly be answered; today they
remain unsupported input, and that is a current limitation rather than a
permanent exclusion." Read every "non-goal"/`by_design_excluded` reference
below in that light — it describes today's actual, shipped stance (option
(A), decided and implemented), not a permanent decision against ever
investigating option (B). A bounded investigation into what a static
archive's symbol/type union could honestly answer remains permitted and, per
the vision, is exactly the kind of lower-priority work that could reopen
this decision — it carries no implied delivery commitment, and nothing here
schedules it. The registry's `by_design_excluded` classification
(`docs/contribute/usecase-registry.yaml`'s `UC-ARCH-static-lib` entry) and
this plan's own index row (`docs/contribute/plans/index.md`) now carry the
same amended wording — `by_design_excluded` still records today's shipped
option-A stance, not a claim that option B is permanently foreclosed.

## Goal & acceptance criteria

Decision gate — choose one and make it explicit:

- **(A) Out of scope:** document `.a`/`.lib` as a non-goal in
  `goals.md` + `concepts/limitations.md`, and have the CLI emit a clear,
  actionable error when handed an archive ("extract members and compare the
  resulting objects/shared library instead"). Flip the registry entry to
  `by_design_excluded` with a `note`.
- **(B) Support link-time API checking:** iterate archive members and analyse
  the union of their symbol/type surface.

Acceptance for **(A)** (recommended first step):
- [x] `goals.md` non-goals and `limitations.md` mention static archives.
- [x] Handing a `.a`/`.lib` to `dump`/`compare` produces a clear error (not a
      traceback or a misleading "not a valid binary").
- [x] Registry entry → `by_design_excluded`.

Acceptance for **(B)** (only if pursued):
- [ ] `ar`-member iteration produces an `AbiSnapshot` over the archive's union
      surface; `compare` works on two `.a`s.
- [ ] An example fixture + `ground_truth.json` entry.

## Design

1. **Detection:** `abicheck/binary_utils.py::detect_binary_format` returns
   `None` for archives today. Add archive detection (`!<arch>\n` magic) so the
   service layer can branch deliberately rather than failing late.
2. **(A) Error path:** `service.resolve_input` raises a `ValidationError` with
   guidance when the input is an archive.
3. **(B) If implemented:** a small `ar` reader (stdlib `arpython`-style, or shell
   out to `ar t`/`ar x` guarded per the no-`shell=True` rule) feeding each member
   object through the existing ELF/COFF/Mach-O object path; union the surfaces.
   Note objects carry no SONAME/dynamic section, so only symbol/type-level kinds
   apply — verdict semantics need a documented caveat.

## Files & surfaces

- `abicheck/binary_utils.py` (archive detection), `abicheck/service.py`
  (branch/raise), `docs/contribute/goals.md`, `docs/learn/limitations.md`.
- (B only) `abicheck/dumper.py` (member iteration), `examples/`.

## Tests

- (A) Unit: archive input → clear `ValidationError`.
- (B) Integration: build a `.a`, dump/compare.

## Out of scope

Thin archives / `ar` with extended naming edge cases unless (B) is chosen.
