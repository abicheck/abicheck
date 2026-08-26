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
exception roots, frozen root filename families, the complete pre-adoption flat
root-module inventory, pre-adoption root directories, and pre-adoption generic
module names. Files outside those inventories must be created under a declared
responsibility package. Scoped package `AGENTS.md` files are also checked
against `limits.package_agents`.

`debt.yaml` has `schema_version: 1` and a `files` list. Every record requires
`path`, positive `baseline_lines`, `target`, `rule: no_growth`, `category`,
`owner`, non-empty `rationale`, and an ISO `review_by` date. Paths are unique,
repository-relative Python files below `abicheck/` or `tests/`, and the recorded
baseline must meet the applicable production or test ceiling. The checker
fails if a tracked file grows; a reduced file is allowed so debt can be paid
down without coordinating a baseline update.

On pull requests, CI passes the base revision through `ARCHITECTURE_BASE`,
set to the PR's own base sha. A `push`-to-`main` or `workflow_dispatch` run
has no PR base at all — `ci.yml`'s "Resolve architecture check base
revision" step sets `ARCHITECTURE_BASE` to the push's own `before` sha (the
tip of `main` immediately prior) for a push, and to the checked-out
revision's own parent commit (`HEAD^`) for a manual dispatch, so either kind
of run is scoped to growth *that run* introduced rather than every file's
original adoption baseline. A push whose `before` is git's all-zero
sentinel (a branch's first push, no prior commit to compare against), and a
dispatch against the repository's very first commit (no parent to resolve),
both leave `ARCHITECTURE_BASE` empty instead. No-growth is then measured
against both the
recorded adoption baseline and the file as it exists at the resolved base:
concurrent changes already present on the base are not attributed to the
architecture PR/push, while any additional growth on top of it still fails.
When the base predates this contract entirely, the run is the adoption run
and records the merged tree rather than treating concurrent pre-adoption
work as new debt.

A local run resolves the same base without `ARCHITECTURE_BASE` set at all:
absent an explicit `--base` or that environment variable, `check_architecture.py`
falls back to a local `git merge-base HEAD <ref>` when one is resolvable,
trying `origin/main` first and a local `main` branch second (a checkout with
no `origin` remote-tracking ref — the remote renamed or removed, a bare
local clone — still gets a base then). Without this, a bare local invocation
compares every debt-tracked file against its original adoption baseline
directly, which drifts over time as unrelated, individually base-scoped PRs
each grow a file a little further — turning an untouched file into a false
failure for the next contributor who runs the documented
`verify.py --profile pr` command. The fallback is silent and best-effort: a
shallow clone or a checkout with neither ref resolvable simply gets the
previous unscoped comparison, exactly as before. This local fallback only
ever triggers when `ARCHITECTURE_BASE` is absent from the environment
entirely — CI's own push/dispatch runs set it explicitly (even to the empty
string), which is a deliberate, different signal from "not set at all" and
must not trigger local git auto-detection: `origin/main` on a push-triggered
runner resolves to `HEAD` itself (the very ref just pushed), which would
silently turn the check into comparing every file against itself instead of
against a real prior revision (Codex review, fresh evidence).
After adoption, an ordinary file absent from the PR base cannot add itself to
the ledger; only files below a declared parser/catalog exception root may use
that mechanism. This keeps the hard file-size ceiling from becoming an
opt-in exception.

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
