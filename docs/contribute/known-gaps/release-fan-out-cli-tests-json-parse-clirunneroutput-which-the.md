---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Release-fan-out CLI tests JSON-parse `CliRunner.output`, which the memory-clamp note can join

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** The clamp note is still a stderr line: `workflows/release_members.compare_release_members` emits it through the reporter's `notice`, which the CLI echoes with `err=True`. tests/test_release_package_inventory_cli.py:92-99 still does `json.loads(CliRunner().invoke(...).output)`, as do test_release_evaluation_config.py:712,730.


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
