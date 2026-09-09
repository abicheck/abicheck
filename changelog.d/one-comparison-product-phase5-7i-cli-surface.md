### Removed

- **`compare --surface-metrics`, `compare --show-filtered` and
  `compare --audit-suppressions` are gone** (ADR-068 D4,
  one-comparison-product.md Phase 5). All three data — ADR-027's
  public-surface metric drift, the ADR-067 scope/disposition ledger, and the
  suppression audit — were already computed on every run, so none of the
  flags decided what was *analyzed*, only whether it was rendered.
  Presentation now goes through one mechanism: `--view filtered` replaces
  `--show-filtered`, `--view suppressions` replaces `--audit-suppressions`,
  and `--surface-metrics` needs no replacement at all because its findings
  are ordinary entries in `changes[]` that every output format already
  renders. The old spellings exit `64` with `No such option` — no hidden
  alias, no deprecation window. `surface_metrics` is also no longer a
  parameter of the documented Tier-2 Python verb
  `abicheck.workflows.compare_policy.compare_snapshots`, which forces it on
  for every caller.
- **`compare --reconcile-build-context` is gone; ADR-039 build-context
  reconciliation now runs unconditionally.** It is strictly evidence-gated —
  a no-op unless both snapshots carry `build_context_defines` and per-field
  guards — and can only ever move a phantom, context-free header-parse
  finding out of the verdict into the audit bucket, never manufacture one,
  so an opt-in switch could only mean "leave a known false positive in your
  verdict because you forgot a flag". Forced on at the one Tier-2
  chokepoint, so the CLI, the typed API and the GitHub Action all get it;
  `CompareRequest.reconcile_build_context` is removed with it, and a
  directory/package (release) comparison — which used to *reject* the flag —
  now gets the behavior too.
- **`compare --pdb-path` is gone** — `debug.pdb_path` in `.abicheck.yml` is
  its only spelling now, the same key `dump` has read since this
  workstream's earlier `debug.*` demotion (ADR-037 D8.1 keeps the two
  commands' debug context from drifting). The flag's per-side `old=`/`new=`
  scoping has no config spelling; a side that needs its own PDB names the
  directory holding it with `--debug-root old=`/`new=`, which the debug
  resolver already searches for one.
- **`compare --support-promise` is gone** — `release.support_promise`
  (`off`/`declared`) in `.abicheck.yml` replaces it. ADR-065 D1/D6, and the
  flag's own help text, already called it a *contract-policy* field: a
  project's declared support promise is a stable property, not a per-run
  operand.
- **`dump --compile-db-filter` is gone** — `build.compile_db_filter` in
  `.abicheck.yml` replaces it, beside the `build.compile_db` it scopes.
  Which subtree of a large shared `compile_commands.json` belongs to a given
  library is a property of the project's layout.

### Changed

- **`dump --git-tag`, `--build-id` and `--no-git` are replaced by one
  repeatable `dump --provenance KEY=VALUE`** (ADR-068 D5,
  one-comparison-product.md Phase 7f). All three were spellings of the same
  thing — stamping a snapshot with where it came from. The keys are
  `git-tag=<tag>`, `build-id=<id>` and `git=auto`/`git=off` (the last being
  the old `--no-git`); a repeated key is last-one-wins, and a malformed
  token, an unknown key, an empty value or an unrecognised `git=` value is a
  usage error rather than a silently dropped stamp. The three old spellings
  exit `64`.
- **`compare --view` gains `filtered` and `suppressions` tokens**, joining
  the existing `full`/`leaf`/`impact`/`root-cause`, `show=<tokens>`,
  `demangle`/`no-demangle` and `patterns`. Both are pure rendering
  selectors over data the run already computed; asking for the suppression
  audit with no `--suppress` stays a no-op rather than a usage error.
  Rejected outright (exit `64`), rather than silently ignored, on a
  directory/package comparison — the release fan-out doesn't render either
  ledger per library yet.
- **`release.support_promise` (`.abicheck.yml`) is rejected together with a
  stored-bundle-facts `OLD_INPUT`**: that driver builds no proven-inventory
  acquisition record to derive a support-promise finding from, so a
  declared policy there would previously have silently produced none.
- **`--view filtered`/`--view suppressions` are rejected for `compare
  --no-baseline`**, the same way every other unsupported view token
  already is: a no-baseline audit reports an empty change set by
  construction, with no scope/disposition ledger and no suppression audit
  to render.
- **`debug.pdb_path` (`.abicheck.yml`) is rejected for a two-operand
  `compare`.** Losing `--pdb-path`'s per-side `old=`/`new=` spelling left
  no way to give two different binaries two different PDBs through this
  key — every consumer's fallback resolved the identical file for both
  sides, and `locate_pdb` honors an explicit override with no check that
  it actually matches the binary it's paired with. Silently sharing one
  PDB across two different builds risks a false clean result. Use
  `--debug-root old=<dir>/new=<dir>` instead — it resolves each side's own
  PDB by that side's binary name.
