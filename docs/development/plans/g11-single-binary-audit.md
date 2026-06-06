# G11 — single-binary ABI audit / lint mode

**Registry:** `UC-WF-audit` (`planned`)
**Effort:** M · **Risk:** low

## Problem

Every command is comparative (old vs new, app vs lib, stack old vs new). There
is no *"scan this one `.so` and report ABI-hygiene problems"* mode — the first
thing a library author wants **before their first release**, when there is no
prior version to diff against. abicheck already captures the substrate
(`elf.has_executable_stack`, `rpath`/`runpath`, `versions_*`, visibility,
symbol table) but exposes no one-sided driver over it.

## Goal & acceptance criteria

- [ ] `abicheck audit <lib>` (or `dump --lint`) runs the single-snapshot subset
      of detectors and emits findings without a second input.
- [ ] Findings cover at least: executable stack, insecure RPATH/RUNPATH,
      missing `SONAME`, unversioned symbols in a versioned library, and
      default-visibility globals that look internal.
- [ ] Output flows through the existing reporter (text/JSON/SARIF) with a
      meaningful exit code; clean libraries pass.

## Design

1. A `SnapshotAudit` pass that takes one `AbiSnapshot` and yields hygiene
   findings, reusing the metadata already on the snapshot model.
2. Reuse the severity/reporting plumbing; audit findings are `RISK`/`quality`
   tier, never ABI-`break` (there is no baseline to break against).
3. The lint rule set is additive — start with the five above, grow over time.

## Files & surfaces

- New `abicheck/audit.py`, a CLI module `abicheck/cli_audit.py`
  (registered per the root `CLAUDE.md` "Adding a new top-level command"),
  reuse of `abicheck/reporter.py`.

## Tests

- `tests/test_audit_lint.py`: snapshots exhibiting each hygiene issue → expected
  findings; a clean snapshot → no findings.

## Out of scope

Cross-binary/stack hygiene (covered by `stack-check`); fix suggestions
(a stated non-goal).
