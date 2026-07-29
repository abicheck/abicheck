<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

<!--
### Added

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Changed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Deprecated

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Removed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
### Fixed

- **Pin `mcp[cli]<2.0.0`** — `mcp` 2.0.0 (released 2026-07-29) dropped
  `mcp.server.fastmcp` entirely (`ModuleNotFoundError` in a clean install),
  so the previously-unbounded `mcp[cli]>=1.2.0` requirement let
  `pip install abicheck[mcp]` (and CI's `pip install -e ".[dev,mcp]"`)
  silently start installing a version that can't import `mcp_server.py`.
- **`abi_compare`'s top-level `changes` array now carries the shadow
  contract-evaluation fields too** — previously, `contract_evaluation=True`
  only stamped `response["report"]["changes"]`, leaving the more commonly
  consumed top-level `response["changes"]` array without
  `contract_relevance`/`contract_reason_code`/`contract_assurance` even
  though the caller had opted in.
- **`used_by`/`required_symbols`-scoped-only findings now get evaluated
  too** — a finding synthesized by app/host scoping *after*
  `compare_snapshots` already ran (e.g. a synthetic
  `consumer_required_symbol_removed`) previously stayed permanently
  unstamped even with `contract_evaluation=True`, since it was never part
  of the collections `compare()`'s own shadow evaluator stamps internally.

<!--
### Performance

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Security

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Documentation

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
