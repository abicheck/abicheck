# G3 — Workflow-scenario examples & Markdown/HTML coverage

**Registry:** `UC-REP-markdown-html` (`partial`)
**Effort:** M · **Risk:** low

## Problem

Two test-breadth gaps:

1. The example catalog (`examples/case*`) is exhaustive about *change types* but
   every case is consumed through the single-pair `compare` workflow. The other
   workflows — `appcompat`, `deps`/`stack-check`, `bundle` — are unit-tested with
   synthetic snapshots, not driven by catalog fixtures. (`tests/test_workflow_scenarios.py`,
   added in this PR, covers the topologies synthetically but does not run the
   catalog through those commands.)
2. **Markdown/HTML reporting** is thinly tested relative to JSON/SARIF/JUnit
   (`tests/test_format_compliance.py`, `tests/test_sprint9_html.py` only), so
   regressions in human-facing output (like the misplaced table delimiter fixed
   in this PR) can slip through.

## Goal & acceptance criteria

- [x] A parametrized harness drives catalog cases through `appcompat` and
      asserts the app-scoped verdict — including the key
      application-centric-filtering property (the same library break is
      BREAKING for an app that imports the removed symbol, COMPATIBLE for one
      that does not). See `tests/test_appcompat_examples.py`.
- [x] A `stack-check` scenario fixture (two sysroots) with an asserted
      stack-level verdict, including a clean control and the CLI surface. See
      `tests/test_stack_sysroot_e2e.py`.
- [x] Markdown and HTML reporters gain structural coverage across verdict tiers
      and the major sections (summary, severity, impact, recommendation,
      confidence) plus HTML escaping. See `tests/test_report_sections.py`.
      (Implemented as in-lane structural assertions rather than committed
      golden snapshots, to avoid brittle full-text fixtures.)

## Design

1. **appcompat-from-catalog:** reuse `examples/case*/app.c|cpp`. For a removal
   case, build the app against v1, then `check_appcompat(app, v1.so, v2.so)` and
   assert `BREAKING` when the app uses the removed symbol, `COMPATIBLE` when it
   doesn't (mirrors `test_workflow_scenarios.py` but end-to-end). Marker:
   `@pytest.mark.integration`.
2. **stack-check fixture:** a tiny two-DSO sysroot pair under
   `examples/` (or `tests/fixtures/`) exercised through `cli_stack.py`.
3. **Reporting breadth:** extend `tests/test_golden_output.py` with cases that
   include the recommendation/impact/severity sections; add HTML structural
   assertions (section presence, escaping) in `tests/test_sprint9_html.py`.

## Files & surfaces

- `tests/test_appcompat_examples.py` (new, integration).
- `tests/test_stack_checker.py` / a new sysroot fixture.
- `tests/test_golden_output.py`, `tests/test_sprint9_html.py` (broaden).
- `tests/golden/*` (regenerate deliberately if Markdown structure is asserted).

## Example fixtures

Reuse existing `app.c|cpp`; add a minimal sysroot pair for stack-check.

## Tests

Primarily test-only; `integration`-marked where compilation is needed. No new
runtime code.

## Out of scope

New change-type example cases (the catalog is saturated there). New report
formats.
