---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: historical
generated: false
---

# Release-fan-out CLI tests JSON-parse `CliRunner.output`, which the memory-clamp note can join

Index: [Known gaps](../known-gaps.md) (closed; see the [archive](../archive/known-gaps-closed.md))

**Status (2026-10-31): CLOSED.** Every test that parses a `CliRunner` document now reads `result.stdout`, never the combined `result.output` stream (136 sites in 51 modules, the release CLI tests among them; shared `_invoke`/`_run` helpers whose other callers assert stderr text gained a `*_stdout` twin for the JSON callers). The clamp note stays a stderr diagnostic. `tests/test_cli_json_reads_stdout.py` scans `tests/` with an intra-module taint walk (direct `json.loads(r.output)`, a local bound from `.output`, or a helper returning it) and fails on a new combined-stream parse; its scanner is unit-tested on each shape. `tests/test_conftest_host_memory_pin.py` now shows, under a forced clamp, that the note precedes the document on `output` while `stdout` stays one JSON document. Residual: on Click 8.0/8.1 (the declared `click>=8.0` floor) a default `CliRunner` still merges the streams into `stdout`; CI runs Click >= 8.2, and the conftest memory pin keeps the clamp from firing in unit runs.


Found while verifying an unrelated change, reproduced on a clean tree, not
fixed here (it is nobody's feature and touches eight tests across two
modules that the change under review did not otherwise touch).

`workflows/release_members.py` echoes one note to **stderr** when the
per-worker memory budget clamps the release fan-out's parallelism ("Note:
parallel release workers reduced 4 -> 3 to fit available memory ..."). Several
release CLI tests capture with a bare `CliRunner()` -- which merges stderr
into `result.output` -- and then `json.loads(result.output)`. When the clamp
fires, the note lands ahead of the document and the parse raises
`JSONDecodeError`, so the test fails for a reason that has nothing to do with
what it asserts.

It only fires under real memory pressure, which is why it is invisible most
of the time and then shows up as a cluster of unrelated-looking failures on a
loaded machine (e.g. the whole suite under `pytest -n 8` on a 16 GiB box).
Reproduced deterministically on a clean tree with
`ABICHECK_RELEASE_JOB_MEM_GIB=1000 pytest
tests/test_release_evaluation_config.py::TestReleaseFanOutAcceptsContractUnresolvedPack`.

Affected (as of this writing): `tests/test_release_evaluation_config.py`'s
`TestReleaseFanOutAcceptsContractUnresolvedPack::test_now_applies_with_contract`
and six tests in `tests/test_release_package_inventory_cli.py`
(`TestPackageArchiveInventoryProvesAbsence`, `TestSupportPromiseFindingsCli`).

The fix is per-test and mechanical: render to a file (`-o PATH`) and parse
that, the way `tests/test_one_comparison_product_parity.py` already does
where `--output-dir`'s own stdout line would otherwise interfere, or capture
stderr separately. Asserting on the note, or suppressing it, would be the
wrong fix -- it is a real diagnostic a user should see.
