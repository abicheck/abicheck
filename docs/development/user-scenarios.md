# User Scenarios & Flows

abicheck must do two jobs well: **classify** an ABI/API change correctly, and be
**usable as a scanner** in a real workflow. The example catalog
([Examples](../examples/index.md)) covers the first exhaustively. The
**user-scenario catalog** covers the second — the *flows*.

A scenario is a real-world **user flow**: a persona, the situation they're in,
the abicheck commands they run, and the outcome they expect. It is an
**internal validation asset** — it drives end-to-end tests — not a user-facing
encyclopedia. It lives next to its runner under `tests/`:

- **Catalog (grouped, globbed):** [`tests/scenarios/*.yaml`](https://github.com/napetrov/abicheck/tree/main/tests/scenarios)
- **Format & index:** [`tests/scenarios/README.md`](https://github.com/napetrov/abicheck/blob/main/tests/scenarios/README.md)
- **End-to-end driver:** `tests/test_scenarios.py`

| Entity | Purpose | Audience |
|---|---|---|
| **Scenarios** (`tests/scenarios/`) | User flows; drive end-to-end *tool* validation (CI gate, compliance scan, SARIF, scoping, offline snapshots). | internal / contributors |
| **Examples** (`examples/`) | Compilable code demonstrating one specific ABI/API *change*. | user-facing |
| **Plans** ([plans/](plans/index.md)) | Backlog of capability gaps to close. | contributors |

## How scenarios drive validation

`tests/test_scenarios.py` merges every `tests/scenarios/*.yaml` (grouped by
theme so the catalog scales past one file) and, for each `automated` scenario,
invokes the abicheck **CLI end-to-end** (via Click's `CliRunner` on JSON
snapshots — no castxml/gcc), asserting the documented exit code / verdict /
SARIF output. The catalog is structurally validated and every scenario's
`validates:` field must point at a real use case in
[`usecase-registry.yaml`](usecase-registry.yaml).

## Capturing missed usage scenarios

When a real-world usage gap surfaces — for example
[issue #235](https://github.com/napetrov/abicheck/issues/235), where private
ABI breaks were reported even when public headers were supplied — it is added
to the catalog (`SC-PUBLIC-SURFACE-SCOPE`) and validated end-to-end so it
becomes a permanent regression guard. This is the mechanism that ensures
abicheck keeps working not just as a change detector but as a scanner tool.

See the [Use-Case Coverage Evaluation](usecase-coverage-evaluation.md) for the
full coverage scorecard and gap plans.
