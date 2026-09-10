### Fixed

- `compare`'s ADR-037 D10.5 visible-flag budget could be exceeded silently.
  The ceiling was `COMPARE_FLAG_BUDGET_BASE + len(COMPARE_FLAG_BUDGET_RAISES)`
  and the check was `visible <= budget`, so every flag removed without
  lowering `BASE` (or removed from `RAISES`) became permanent slack a later
  flag could occupy with no written rationale — contradicting the ledger's own
  documented guarantee. Measured before the fix: 48 visible flags against a
  budget of 57, nine flags of slack, with `--budget` already landed carrying
  no ledger entry. The ceiling is now exactly the number of options carrying a
  written ADR-068 D5 ruling, asserted as an exact bijection in both
  directions, so there is no slack to consume.

### Changed

- Every visible `compare` **and** `dump` option now carries a written ADR-068
  D5 ruling in `abicheck/frontends/cli/options/rulings.py`, saying which of
  D5's three guards lets it stay (a per-invocation operand; not a second
  spelling of an existing concept; not an escape hatch that disables real
  analysis). `dump` had no ledger of any kind before this, despite ADR-037
  D8.1 requiring its shared families not to drift from `compare`'s. A ruling
  is either a keep or a `deferred` demotion naming the unlanded prerequisite
  blocking it; the two are structurally distinguishable, so a deferral cannot
  become a permanent keep by nobody re-reading it.

  This is internal contract metadata — no option was added, removed, hidden or
  renamed, and no verdict, gate, exit code or report field changes.
  `COMPARE_FLAG_BUDGET_BASE` and `COMPARE_FLAG_BUDGET_RAISES` are gone from
  `abicheck.cli_options`; `COMPARE_FLAG_BUDGET` remains, now equal to the size
  of the ruled set, and `DUMP_FLAG_BUDGET` joins it.

- `dump --compression` is explicitly ruled a per-run operand rather than
  something inferable from the `-o` suffix: `-o build/abi.json --compression
  zstd` (a CI job publishing a fixed artifact name) is a real case suffix
  inference cannot express. No behavior change — the flag and its
  `snapshot-compression` Action input were already there; the plan's prior
  classification of it as removable is what changed.
