---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Re-scoping a cache-reloaded snapshot keeps typedefs that scoping the fresh extraction drops

Index: [Known gaps — closed](../archive/known-gaps-closed.md)

**Status (2026-10-31): CLOSED.** Root cause identified and fixed: the
attribution scoping reads for the flat `constants`/`typedefs`/`typedefs_qualified` maps lives in a
runtime-only `HeaderAttributedMap`, which the snapshot codec deliberately does not persist (a loaded
baseline must not license its own attribution). A reloaded snapshot therefore carried plain dicts, and
`flat_map_dependency_scope._kept_keys` kept every entry. The dump cache now stores the attribution in a
cache-private sidecar (`workflows/dump/cache/declaring_headers.py`) and re-attaches it on read; a
header-derived entry missing its sidecar is a miss. Reproduced on libssl.so.3 with `-H openssl/ssl.h`
(fresh scope 652 typedefs, reloaded 821) before the fix; equal after. Tests:
`tests/test_dump_cache_rescope_roundtrip.py`.

**Original status (2026-10-22): OPEN.** Found while adding the finished-snapshot dump-cache tier (PR #1726).

`scope_snapshot_excluding_dependencies` applied to a snapshot reloaded from the raw dump cache is not
equal to the same call on the freshly extracted snapshot: on `libssl.so.3` with `-H openssl/ssl.h`
the reloaded side keeps typedefs such as `ASN1_ENCODING` (and their `semantic_ir` / typedef-id
entries) that the fresh side drops. So before #1726 a warm run could differ from a cold one.

The finished tier is written only from a fresh extraction, and a finished-tier miss re-extracts
rather than re-scoping the raw cache entry, so warm runs through `resolve_input` reproduce cold
output (a cache populated before #1726 pays one extra extraction per operand, once). The asymmetry
itself remains for any other caller that scopes a deserialized snapshot. Root cause (which state the
serialization round-trip loses or adds that scoping reads) is not yet identified.
