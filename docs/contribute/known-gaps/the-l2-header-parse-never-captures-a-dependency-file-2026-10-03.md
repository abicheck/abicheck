---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The L2 header parse never captures a dependency file (2026-10-03)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-25): OPEN.** `depfile_resolved_paths` is a parameter of `abicheck/comparability.py` (line 314, forwarded at 443) and named in a `header_conditionals.py` docstring; no production caller passes it.


Also from Stage E: `compute_extraction_contract`'s `depfile_resolved_paths`
and `generated_driver_path` are never passed, because no castxml/clang L2
invocation requests `-MD -MF`. ADR-050 D1 describes `include_sequence` as
hashing the content of every file the parse actually read under each external
`-I` root, plus a system/toolchain bucket; with no depfile both are always
empty. Two dumps that read different dependency headers (a newer
`/opt/dep/include`, a libstdc++ update changing an ABI-relevant macro) get
identical `profile_fingerprint`s, the under-counting this digest exists to
prevent. `buildsource/include_graph.parse_depfile` already parses the format.
Building the capture changes fresh fingerprints the same way the target
platform entry above does, so it needs the same unrecorded-side carve-out.
Owner: ADR-050; until then ADR-050 D1 overstates what is fingerprinted.
