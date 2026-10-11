---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The release public surface is acquired once, but each member snapshot still stores its own copy of the header evidence

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** The per-member header evidence is still stored once per member. abicheck/storage/import_bundle_facts.py:107 still has _BUNDLE_FACTS_SCHEMA_VERSION = 3, so it still refuses schema 4.


Recorded with the release product model (2026-09-17), measured rather than
assumed. The *output* cardinality is closed: the contract is reconciled once,
a shared finding is rendered once, and a release's report size now scales as
O(public surface + members + real findings) — 8.7 MiB -> 77 KiB on a
12-library / 480-declaration fixture. Two costs are not closed:

1. **Storage.** Every entry in `BundleFacts.per_library_snapshots` still
   carries its own copy of the header evidence its dump parsed.
   `BundleFacts.public_surface` (schema 4) gives the *product contract* one
   canonical home and records the acquisition identity it was acquired
   under, which is what lets a stored baseline be reconciled against the
   union of its members' exports — but it does not deduplicate the
   per-member header evidence beside it. Doing that is a change to snapshot
   persistence itself (a shared-surface reference plus a migration for every
   existing baseline), not to the release layer, which is why it was
   recorded rather than half-done here.
2. **One extra header parse per side.** The release-level surface is acquired
   through `header_only_dump.build_header_only_snapshot`, whose acquisition
   key legitimately differs from the member dumps' own (a header-only parse
   resolves its language mode without a binary's export evidence), so it
   does not hit the AST cache entry the member dumps share. Measured on the
   two-library fixture: 2 castxml cache entries before, 4 after — i.e. two
   parses per side rather than one, and **O(1) per side either way**, never
   O(members). The requirement it does meet is the one that mattered:
   castxml is not invoked once per library for an identical acquisition
   request, and the release acquisition ledger reports exactly one
   acquisition per side (`public_surface_reconciliation.acquisition`).
   Closing it means letting the release surface reuse a member dump's
   already-parsed header AST under the *same* acquisition key, which is a
   change to how `dumper`'s language-mode resolution keys a header-only
   parse — worth doing, and not a correctness issue in the meantime.

3. **The `ProjectSnapshot` import adapter refuses a schema-4 document.**
   `storage/import_bundle_facts.py` has no composition section for
   `public_surface`, so it keeps its own `_BUNDLE_FACTS_SCHEMA_VERSION = 3`
   and its existing "newer than this build knows how to interpret" gate
   refuses such a document outright. That is deliberate, and it is the
   fail-closed half of "no silent reinterpretation": carrying the version
   forward without the block would import members whose recorded contract
   had been discarded, leaving them reconciled against nothing. It is still
   a gap. The adapter has no in-tree production caller today (it is a
   public storage API exercised by tests), which is why teaching it a
   `public_surface` composition section -- another `storage/dto.py` section
   version -- was recorded rather than done here.
   `tests/test_release_public_surface.py::TestBundleFactsSchema::test_the_project_snapshot_importer_refuses_a_v4_document`
   states the refusal so it cannot silently become a drop.

Also open, and smaller: the JUnit projection of a release carries per-member
test cases only, so the release-level public-surface section appears in the
JSON and Markdown renders but not there. The gate is unaffected — a
release-level contract finding folds into `worst_verdict` and the exit code
before any format renders — so this is a traceability gap in one format, not
a missed finding.
