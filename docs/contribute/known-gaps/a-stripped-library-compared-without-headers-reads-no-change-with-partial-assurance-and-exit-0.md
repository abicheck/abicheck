# A stripped library compared without headers reads NO_CHANGE with partial assurance and exit 0

Opened 2026-11-08. The index entry is in [known-gaps.md](../known-gaps.md).

**Status (2026-11-08): OPEN.** Found while building the invariant oracles (`tests/test_invariant_oracles_reduced_evidence.py`). A shared object with its symbols stripped (`strip`, no `.symtab`/DWARF) compared against a changed build with no `-H` headers reads `verdict: NO_CHANGE`, `analysis_assurance.status: partial`, exit 0 and `Gate: ACCEPTED`. The same pair unstripped reads `BREAKING` (`func_params_changed`, exit 4) with `complete` assurance, so the change is real and the reduced run loses it: a parameter-count change on an exported symbol is not visible from the stripped dynamic symbol table alone. The terminal headline also says `Gate: ACCEPTED` while its own warning says "Treat this result as unconfirmed". Invariant A (`tests/oracles/absence.py`) says a `partial` run must not state a compatibility claim, so this is a false-clean on missing evidence. Owner: `workflows/dump/` (the symbols-only fallback) and the verdict/gate mapping in `report/`.
Two more shapes read clean the same way, and the module pins all three (`test_stripped_pair_gap_is_still_the_current_answer`, parametrized):

| Run | `verdict` | `analysis_assurance.status` | `version_bump` | exit |
|---|---|---|---|---|
| both stripped, no headers | `NO_CHANGE` | `partial` | `none` | 0 |
| old with DWARF, new stripped, no headers | `COMPATIBLE` | `partial` | `patch` | 0 |
| both stripped, `--depth headers`, no headers | `NO_CHANGE` | `failed` | `none` | 0 |

Every renderer agrees with the clean claim: terminal `Gate: ACCEPTED`, Markdown and review `` `NO_CHANGE` `` with review's `**Gate:** PASS` and "safe to merge", HTML `Compatibility: NO_CHANGE`. The controls hold: DWARF with no headers reads `BREAKING`/exit 4 with `complete` assurance, a DWARF no-change pair reads `NO_CHANGE`/exit 0 with `complete` assurance, and a header extractor that fails (unparseable header) or is unavailable (castxml not on `PATH`) stops the run with an error rather than comparing an empty surface.

The fix is [ADR-090](../adr/090-unresolved-surface-is-not-a-clean-verdict.md) (proposed): verdict `UNCONFIRMED`, `version_bump: undetermined`, exit 1. The strict xfails in the module state that contract as three separate checks (JSON claim, exit code, rendered wording), so each flips as the ADR's stages land.

`false-clean` — a compare without type-level evidence on a side reads a clean verdict with exit 0 and an accepted headline.
