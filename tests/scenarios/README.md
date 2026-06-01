# User-scenario catalog (internal validation asset)

These YAML files define **user flows** — how real people use libraries,
binaries, and abicheck — and drive **end-to-end validation** that abicheck
works as a usable scanner tool, not only an ABI/API-change detector. They are
consumed by `tests/test_scenarios.py`; this is an *internal* test asset, not a
user-facing artifact (contrast `examples/`, which is a user-facing change-type
encyclopedia, and `docs/development/plans/`, the capability backlog).

## Layout — grouped files, merged by globbing

The catalog is split into one file per group so it scales without a single huge
file. `tests/test_scenarios.py` merges every `tests/scenarios/*.yaml`:

| File | Group |
|---|---|
| `ci_gating.yaml` | CI ABI gating — exit-code contract, severity gate, `--stat`, malformed-input degraded mode |
| `compliance_scanning.yaml` | public-surface scoping (issue #235), suppression (+ expiry), policy profiles |
| `change_archetypes.yaml` | per-archetype canonical breaks — C struct layout, C++ vtable, exported data |
| `toolchain_coverage.yaml` | build/toolchain-driven shifts — dual-ABI flip, LP64↔ILP64, flag drift |
| `reporting.yaml` | report formats — JSON contract, SARIF, JUnit, HTML, review digest |
| `release_management.yaml` | release recommendation, offline snapshots, baseline registry |
| `consumer_deployment.yaml` | compare-release, appcompat, stack-check/deps, ABICC, Debian, MCP (planned) |
| `platform_coverage.yaml` | Linux ELF baseline (automated); native Windows PE/macOS Mach-O, plugin host↔plugin contract (planned) |
| `archetype_coverage.yaml` | kernel-BTF, SYCL plugin, static library, header-only (planned) |

There are currently **44 scenarios** (28 automated end-to-end + 16 planned).

Add a new group by dropping in a new `*.yaml`; add a scenario by appending to an
existing group. Scenario ids must be unique across all files.

## Scenario format

```yaml
schema_version: 1
scenarios:
  - id: SC-SOMETHING            # unique, SC-…
    title: Short imperative title
    persona: Who is doing this
    narrative: >
      The real-world situation in prose.
    flow:                       # the abicheck commands the user runs
      - abicheck compare old.json new.json
    expected:                   # what the automated test asserts
      exit_code: 4
      verdict: BREAKING
    validates: UC-WF-compare    # an id in docs/development/usecase-registry.yaml
    automated: true             # has an end-to-end test
    test: test_sc_something      # function in tests/test_scenarios.py
    # issue: 235                # optional: the issue that surfaced this flow
```

Planned (not yet automatable) scenarios set `automated: false`,
`status: planned`, and link a `plan:` (under `docs/development/plans/`) or carry
a `note:`.

## How it is validated (`tests/test_scenarios.py`)

- structural validity + unique ids across all files;
- every `validates:` points at a real use case in the registry;
- every `automated: true` scenario has a `test:` that invokes the abicheck CLI
  end-to-end (CliRunner on JSON snapshots — no castxml/gcc) and asserts the
  documented `expected` outcome;
- every non-automated scenario is `status: planned` with a plan or a note.

## Capturing missed usage scenarios

When a real-world usage gap surfaces (for example
[issue #235](https://github.com/napetrov/abicheck/issues/235) — private ABI
breaks reported despite public headers), add it here so it becomes a permanent
end-to-end regression guard.
