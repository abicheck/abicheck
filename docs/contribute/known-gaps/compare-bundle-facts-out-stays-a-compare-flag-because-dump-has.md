---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: historical
generated: false
---

# `compare --bundle-facts-out` stays a `compare` flag because `dump` has no release fan-out

Index: [Known gaps](../known-gaps.md) (closed; see the [archive](../archive/known-gaps-closed.md))

**Status (2026-10-31): CLOSED.** `abicheck dump RELEASE --bundle-facts-out PATH` captures a directory, package archive or stored `ProjectSnapshot` package as a `BundleFacts` document with no comparison (`workflows/release_bundle_facts_dump.py`, CLI adapter `frontends/cli/commands/dump_bundle_facts.py`). It composes the comparison's own owners rather than a second producer: the one-sided release resolver (`no_baseline_set.resolve_no_baseline_set_plan`, now taking the side's headers/includes), the member-dump resolver `workflows/release_member_dump.dump_release_member` (moved out of `release_compare._resolve_stranded_library`, which now delegates to it), the release public-surface acquisition, and `write_bundle_facts_out` given no member pairs. It applies `-H`/`--include`/`--exclude-header`/`--label`, the config's compile settings and `--variant ID` (unsided; a stored package declaring several variants), replaces `-o`, and refuses the dump inputs it does not apply (`--dry-run`, `--dump-manifest`, `--sources`, `--build-info`, `--debug-info`, `--provenance`, `--follow-deps`). Ruling (2026-10-31, maintainer-approved): `compare --bundle-facts-out` stays, not deprecated -- it captures the OLD side a comparison already extracted, at no second extraction; both spellings write through one producer (`frontends/cli/options/rulings.py`). The parity test found a producer bug, fixed here: `compare --bundle-facts-out` keyed a paired member by its `DiffResult`'s library name, so a JSON-snapshot member was persisted twice (under its file name and its snapshot's `library`). Pairs now carry the member's own file name (`release_snapshot_retention.release_old_member_snapshots`), so every key is a member key and a snapshot the run already holds is never extracted again. Tests: `tests/test_dump_bundle_facts.py` (dump's document == compare's for five release shapes; `--variant`; refusals; no re-extraction) and `tests/test_dump_bundle_facts_integration.py` (gcc-built libraries with and without headers: equal evidence to compare's, each member equal to that library's own `abicheck dump`, and `compare DUMPED NEW` reaching `compare OLD NEW`'s verdicts). Not supported: debug/devel packages (`compare`'s `--debug-info old=PKG`).

## History

Recorded in 2026-10, before the fix. Capturing a release's per-library
snapshots into a `BundleFacts` document is a *dump* operation -- it reads
one release and does not need a NEW side -- but only `compare` could turn a
directory or package into per-library snapshots, so the capture rode on a
comparison as `--bundle-facts-out`. Writing a `dump` fan-out first needed
the release input-resolution chain moved engine-side; that move
(`workflows/release_inputs.py`, typed `errors.ReleaseOperandError`)
happened in an earlier pass, which left the fan-out unblocked but unwritten.
The plan then was to retire the `compare` flag once `dump` could write the
document; the 2026-10-31 ruling above kept both instead.
