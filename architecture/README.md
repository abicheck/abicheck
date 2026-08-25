# Architecture contract

This directory is the machine-readable enforcement surface for
[ADR-061](../docs/contribute/adr/061-responsibility-package-architecture.md).
It separates the desired dependency graph from temporary migration debt.

## Files

- `modules.yaml` declares the eight responsibility packages, their exact
  first-party dependency direction, file-size ceilings, supported root
  surfaces, and adoption-time inventories used to freeze legacy flat
  namespace families. Although the file uses JSON syntax, JSON is a strict
  subset of YAML; keeping the document in that subset lets the architecture
  gate run before third-party dependencies are installed.
- `debt.yaml` records every production Python module above ADR-061's 800-line
  ceiling and every test module above its 1,200-line ceiling at adoption. Each
  entry freezes its measured line count and gives a target owner, category,
  maintainer, rationale, and review date. It is a no-growth ledger, not an
  import allowlist, and should only shrink as vertical migrations land.
- `scripts/check_architecture.py` validates both documents and enforces them.

## Schema

`modules.yaml` has `schema_version: 1`, a `limits` mapping, and a `layers`
mapping. Every layer record requires a repository-relative `path` and a
`may_import` list naming other declared layers. During migration, an optional
`legacy_paths` list classifies flat modules by their target owner so dependency
direction can be enforced before their own physical move. Paths must be unique; imports
must name real layers; and the resulting graph must be acyclic. The remaining
lists classify public root surfaces, compatibility facades, parser/catalog
exception roots, frozen root filename families, pre-adoption root directories,
and pre-adoption generic module names.

`debt.yaml` has `schema_version: 1` and a `files` list. Every record requires
`path`, positive `baseline_lines`, `target`, `rule: no_growth`, `category`,
`owner`, non-empty `rationale`, and an ISO `review_by` date. Paths are unique,
repository-relative Python files below `abicheck/` or `tests/`, and the recorded
baseline must meet the applicable production or test ceiling. The checker
fails if a tracked file grows; a reduced file is allowed so debt can be paid
down without coordinating a baseline update.

On pull requests, CI passes the base revision through `ARCHITECTURE_BASE`.
No-growth is then measured against both the recorded adoption baseline and the
file as it exists on the PR base: concurrent changes already present on the
base are not attributed to the architecture PR, while any additional growth
on the branch still fails. When the base predates this contract entirely, the
run is the adoption run and records the merged tree rather than treating
concurrent pre-adoption work as new debt.

## Updating the contract

Do not raise a baseline to make a check pass. Move a responsibility with its
tests, switch internal imports to the new owner, then lower or remove the debt
entry. A new responsibility package must match `modules.yaml`, include a
scoped `AGENTS.md`, and obey the declared import graph. Changes to the stable
graph require architectural review and an ADR amendment rather than a debt
exception.

Run the focused gate with:

```bash
python scripts/check_architecture.py
```

It is also the `architecture` step in `python scripts/verify.py --profile pr`.
