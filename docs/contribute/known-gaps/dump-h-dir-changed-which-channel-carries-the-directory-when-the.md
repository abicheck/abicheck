---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `dump -H <dir>` changed which channel carries the directory when the ELF `dump` CLI migrated onto the shared typed executor — recorded, deliberately not reverted (ADR-063 Track 1, 2026-09-05)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** service_dump_pipeline.py:347 splits -H and passes directories as public_header_dirs, which enables provenance tagging. scope_header_dirs is now set only at the dumper.py level (dumper.py:411), as the entry says. The entry records this as deliberate convergence with compare, not a defect.

Found while retiring
`cli_dump_helpers.perform_elf_dump`: the one assertion in its test suite
that could not be rehomed, because the behaviour it pinned is no longer
the behaviour. `perform_elf_dump` split its `-H` list with
`header_utils.split_public_header_inputs` and routed the *directory* half
into `dumper.dump`'s `scope_header_dirs` — folded into the extraction
contract's scope, with ADR-015 declaration-provenance tagging
deliberately left off (`tests/test_dumper_contract_wiring.py::
test_scope_header_dirs_does_not_enable_provenance_tagging` still pins that
distinction at the `dumper` level, which is where it lives). The typed
request performs the identical split in `service_dump_pipeline.
resolve_dump_request` but passes the directory as a real
`public_header_dirs` entry, so a plain `dump -H include/` now *does* tag
provenance for everything under it. `scope_header_dirs` has no typed-request
field populating it at all any more — `api_types.py` says as much, where it
excludes the field from the `dump_manifest` mutual-exclusivity set.

**Why it stands.** This is convergence, not drift: `compare` has always
treated its own `-H` list this way, and `dump`'s own
`--public-header/--public-header-dir` pair — removed earlier precisely for
saying the same thing a second way — is what the new behaviour matches. The
original comment on `perform_elf_dump`'s fold gave "so a `dump --header
<dir>` baseline and a live `compare --header <dir>` candidate of the
identical header set agree on `scope_fingerprint`" as its whole purpose;
both commands now reach that agreement through one shared split rather than
two hand-maintained ones. Reverting would mean re-introducing a `dump`-only
channel and a `dump`-vs-`compare` provenance asymmetry to preserve a
behaviour whose own stated goal the convergence already serves.

**What is *not* established.** Nobody has measured whether the added
provenance tagging changes any real snapshot's classification for a
directory operand that contains genuinely private siblings — the analogous
hazard the `public_include_search_dirs` rule exists for (an auto-derived
umbrella directory holding a private sibling header). The difference is
that `-H <dir>` is an *explicit operand*, not an auto-derived one, and
declaring a directory public is exactly what typing it means; that is the
reasoning, not a measurement. If a real case turns up where it is wrong,
the fix is at the split (`split_public_header_inputs`' contract, shared by
both commands), not by restoring a per-command channel.
`tests/test_dump_cli_execution_behaviors.py::
test_dump_header_directory_reaches_the_extraction_scope` pins the current
channel with this reasoning attached, so the next reader finds the decision
rather than re-deriving it from a diff.
