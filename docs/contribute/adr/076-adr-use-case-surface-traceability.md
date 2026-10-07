# ADR-076: ADR, Use-Case, Public-Surface and Scenario Traceability

**Date:** 2026-10-07
**Status:** Accepted — implemented. Adds
`docs/contribute/adr/adr-surface-registry.yaml`, the `user_task:`/`adrs:`
fields on `docs/contribute/usecase-registry.yaml`, the optional
`surfaces:`/`family:` fields on `tests/scenarios/*.yaml`, and the gate
`scripts/check_adr_surfaces.py` (ratchet baseline
`docs/contribute/adr/adr_surface_baseline.json`, generated report
[`docs/contribute/generated/adr-surface-coverage.md`](../generated/adr-surface-coverage.md)), run as `verify.py`'s
`adr-surfaces` step.

## Context

Three registries describe what abicheck does, and none of them pointed at the
others:

- the **ADRs** (`docs/contribute/adr/`) say what was decided and, in their
  Status line, whether it is implemented;
- the **use-case registry** (`docs/contribute/usecase-registry.yaml`) says
  which application/library ABI-API cases are covered, with evidence paths;
- the **scenario catalog** (`tests/scenarios/*.yaml`) records user flows as
  real `abicheck ...` command lines, each validating one use case.

Nothing recorded which public surface — a CLI command or flag, a typed Python
API symbol, a GitHub Action input, a report field or output format — makes an
ADR's decision reachable by a user. So an ADR could read "Accepted —
implemented" while nothing a user can invoke reached it, a surface could be
renamed away under an ADR with no signal, and a use case reachable from the
CLI, the Python API and the Action had no requirement that one scenario
family exercise all three. `vision.md`'s user tasks (PR review, local check,
release, audit) were not attached to use cases at all, although `AGENTS.md`
asks every change to establish its user task first.

## Decision

### D1 — One registry entry per ADR, with a disposition

`adr-surface-registry.yaml` holds exactly one entry per ADR file (the
duplicate-numbered 020/021 pairs use the index's `a`/`b` labels). Each entry
has a **disposition**:

| Disposition | Meaning | Required field |
|---|---|---|
| `surfaced` | a public surface reaches the decision | at least one `surfaces` item |
| `partial` | some of it is reachable | `missing` (what is not) |
| `gap` | nothing reaches it yet | `missing`; no `surfaces` |
| `internal` | it has no user surface by nature (architecture, process, retired) | `reason`; no `surfaces` |

and the `use_cases` (`UC-*` ids) it serves. A surface is one of four kinds:
`{cli: "<command path> [--flag ...]"}`, `{api: "<dotted.symbol>"}`,
`{action: "<root input>" | "actions/<name>" | "actions/<name>:<input>"}`,
`{report: "<schema>.<top-level field>" | "format:<compare -o value>"}`.

### D2 — Surfaces are checked against the code, not trusted

`check_adr_surfaces.py` resolves every surface: CLI commands and flags by
introspecting the click tree of `abicheck.cli.main` (the same source the
generated CLI reference uses), API symbols by import, Action inputs from
`action.yml` / `actions/<name>/action.yml`, report fields from
`abicheck/schemas/<schema>.schema.json`'s top-level `properties`, report
formats from `compare -o`'s choices. A renamed or deleted surface fails the
gate where it is cited.

### D3 — Disposition must agree with the ADR's Status

The index Status column (already kept in sync with each ADR's own Status by
`adr-status-sync`) is classified: *retired* (Deprecated/Superseded) must be
`internal`; *implemented* — the word "implemented" with no qualifier such as
partially, substantially, phased, deferred, not yet, roadmap, remains, still
or slice — must be `surfaced` or `internal`. Anything else may take any
disposition. The classifier is deliberately conservative: a qualifier sends
an ADR to the permissive class rather than forcing a false claim.

### D4 — Use cases carry their user task and mirror the ADR links

Every UC entry gains `user_task:` (a non-empty list from `pr_review`,
`local_check`, `release`, `audit`, the vision's user tasks) and `adrs:`. The
`adrs:` list must equal the ADRs whose registry entry lists that use case, so
the link is stated in both places but can only be edited consistently.

### D5 — Scenarios trace surfaces through their real commands

A scenario may declare `surfaces:` (same kinds as D1) and a `family:`. A
declared `cli` surface must be *used* by one of the scenario's own `flow`
commands — same command path, every declared flag present (aliases such as
`-o`/`--output` resolve to one parameter). A scenario therefore cannot claim
to cover a flag its command line does not pass.

### D6 — Ratchet, never silent regression

`adr_surface_baseline.json` records each ADR's disposition, surfaces and use
cases, the ADR surfaces no scenario traces, and the use cases reachable from
all three of cli/api/action that no shared scenario `family` covers. The gate
fails on a disposition that is not an upgrade along `gap → partial →
surfaced` (any move onto or off `internal` counts as a reclassification), a
lost surface, a lost use case, a newly untraced ADR surface, or a newly
uncovered multi-channel use case. Accepting one is an explicit
`--write-baseline` committed in the same PR, where review sees it.
Improvements need no rewrite but may tighten the baseline.

### D7 — A generated coverage report

The gate renders `docs/contribute/generated/adr-surface-coverage.md` (counts
per disposition, the per-ADR table, use cases per user task, and both ratchet
lists) and fails when the committed copy is stale
(`--write-report` regenerates it).

## Consequences

- Adding an ADR adds a registry entry in the same PR; adding, renaming or
  removing a CLI flag, Action input, API symbol or report field that an ADR
  cites updates the registry in the same PR.
- The initial population is a reading of each ADR, recorded honestly:
  where reachability was uncertain the entry says `partial` or `gap` with
  `missing`, never `surfaced`. The untraced-surface list starts long — no
  scenario declared `surfaces:` before this ADR — and only shrinks.
- The gate needs abicheck importable (click introspection, API imports), so
  it runs after `pip install -e .`, alongside `usecase-docs-sync`, not inside
  the pure-stdlib `check_ai_readiness.py`.

## Alternatives considered

- **Parsing the ADR prose for surfaces.** Rejected: free text names flags
  that were later removed (ADR-040's `--profile`) and would need a second
  source of truth to say which mentions are current.
- **Putting surfaces into ADR front matter.** Rejected for the same reason
  the index records why ADRs have no structured front matter: retrofitting
  77 files with metadata the gate must then parse out of Markdown is less
  checkable than one YAML registry.

Concepts adapted from another project's traceability tooling.
