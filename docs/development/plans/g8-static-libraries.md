# G8 — Static-library (`.a` / `.lib`) stance

**Registry:** `UC-ARCH-static-lib` (`planned`)
**Effort:** S (decision) → M (if implemented) · **Risk:** low

## Problem

Static libraries are **unhandled and undocumented** — they are not supported and
not listed as a limitation or a non-goal. A user pointing `abicheck` at a `.a`
gets no clear answer. The stated non-goals (`docs/development/goals.md`) cover
runtime instrumentation, fix suggestions, and non-C/C++ languages, but say
nothing about static archives.

This plan's first deliverable is a **decision**, not code.

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
- [ ] `goals.md` non-goals and `limitations.md` mention static archives.
- [ ] Handing a `.a`/`.lib` to `dump`/`compare` produces a clear error (not a
      traceback or a misleading "not a valid binary").
- [ ] Registry entry → `by_design_excluded` (or stays `planned` if (B) is chosen).

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
  (branch/raise), `docs/development/goals.md`, `docs/concepts/limitations.md`.
- (B only) `abicheck/dumper.py` (member iteration), `examples/`.

## Tests

- (A) Unit: archive input → clear `ValidationError`.
- (B) Integration: build a `.a`, dump/compare.

## Out of scope

Thin archives / `ar` with extended naming edge cases unless (B) is chosen.
