<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **The contract-coverage exit is now applied, not just reported (ADR-049
  Phase 7).** `compare` and `scan --against` have emitted
  `contract_coverage_exit_contribution` since the coverage ledger landed, and
  it was deliberately inert — the point was to let a run show what the flip
  would do before it did it. It now moves the process exit status. Under
  `--contract-evaluation`, a selected contract domain that cannot close on the
  evidence available (for example `--contract exports` against a snapshot pair
  carrying no export table) contributes exit **1**.

  The axis is orthogonal, exactly as ADR-049 §7 specifies: it is folded with
  `max`, so a coverage failure raises a clean `0` to `1` and can never lower a
  gate's `2`/`4` — a real ABI break is never demoted to "warnings only" by
  missing coverage. It equally never rewrites a finding's compatibility
  decision or its gate contribution; it is a floor on the exit status and
  nothing else. `compare` and `scan --against` fold it identically (§6.4's
  cross-command parity Gate).

  **A run that does not pass `--contract-evaluation` is unaffected**: with no
  selected domain there is nothing to be short of evidence for, so every
  pre-existing invocation exits exactly as before.

  To accept incomplete contract assurance, set `contract.unresolved: warn`
  (ADR-049 D9) — the documented mechanism for precisely this. It zeroes the
  coverage contribution and changes nothing else: the failures stay in
  `contract_coverage_failures`, stay unsuppressible, and stay in every report.
  Accepting incomplete assurance is not the same as hiding it. The reported
  `contract_coverage_exit_contribution` is now the *applied* number, derived by
  the same function the exit path uses, so the ledger a user reads is the one
  that gated them.

- **The MCP `abi_compare` tool folds the same axis, and explains it.** Its
  `exit_code` is subject to the identical `max` fold, so a client gating on the
  returned code can no longer accept a run the returned report says was gated.
  Because only the JSON report carries the ledger, the response gained a
  top-level `contract_coverage` block — the contribution, the failure list, and
  a one-line diagnostic — emitted for every `output_format` rather than only
  the one whose report happens to include it.

- **The composite GitHub Action publishes `verdict: COVERAGE_INCOMPLETE`** for
  a run gated by this axis, instead of misreporting it as an operational
  failure (`ERROR`, on `scan`, which previously had no mapping for exit 1 at
  all) or a severity-policy failure (`SEVERITY_ERROR`, on `compare`). On
  `compare`, where the two axes genuinely share exit 1, they are told apart by
  the report's pre-fold `severity.exit_code` rather than guessed from the code:
  when the severity gate produced 1 too, its verdict stands and the coverage
  contribution is reported alongside. A run without `--contract-evaluation`
  maps exactly as before — including in the documented `format: json` with no
  `output-file` mode, where the report exists only on stdout and neither of the
  mapping's signals had a file to read.

  The MCP tool's own diagnostic says how *its* caller sees the full ledger and
  what accepting incomplete coverage would take: `contract.unresolved: warn`
  has no source other than a `kind: contract` pack, and `abi_compare` exposes
  no pack parameter, so it is a `compare`/`scan` control today and the tool
  says so rather than recommending one its callers cannot reach.

  `compare --help`, `scan --help`, and both commands' `--dry-run` exit-code
  summaries now list the orthogonal coverage exit alongside the scheme they
  already documented, so a CI integration reading a command's own exit-code
  contract does not meet exit 1 as an undocumented failure — or, under the
  severity scheme, mistake it for a severity error.

  The Action reads the report with Python rather than `jq`. It never installed
  `jq` — GitHub-hosted runners happen to ship it, self-hosted ones need not —
  and on a runner without it a JSON-format coverage-gated run had no signal at
  all, since the CLI deliberately prints no stderr notice when the report
  already carries the ledger. Python is the dependency the Action really has:
  it runs `actions/setup-python`, and `abicheck` is itself a Python console
  script. All four of its report lookups now share that one parser.

### Added

- **`contract.unresolved` is now an applied `kind: contract` pack field.** It
  was rejected as "resolvable but not applied by this build" until its engine
  consumer existed; the coverage exit above is that consumer, so a contract
  pack may now assign it. It is the one applied pack field that is not folded
  into a legacy object — its consumer reads it straight off the resolved
  configuration the receipt already persists.

  Assigning it **without `--contract-evaluation` is a usage error (exit 64)**
  on both `compare` and `scan --against`: nothing computes coverage unless a
  domain is selected, so the value would be recorded as active configuration
  and read back as nothing. That is the decorative-pack failure the pack
  application layer exists to prevent, one level in — the field is applied by
  this build, just not by that invocation.
