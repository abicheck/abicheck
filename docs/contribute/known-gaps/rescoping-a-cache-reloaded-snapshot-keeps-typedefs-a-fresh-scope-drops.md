---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Re-scoping a cache-reloaded snapshot keeps typedefs that scoping the fresh extraction drops

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-22): OPEN.** Found while adding the finished-snapshot dump-cache tier (PR #1726).

`scope_snapshot_excluding_dependencies` applied to a snapshot reloaded from the raw dump cache is not
equal to the same call on the freshly extracted snapshot: on `libssl.so.3` with `-H openssl/ssl.h`
the reloaded side keeps typedefs such as `ASN1_ENCODING` (and their `semantic_ir` / typedef-id
entries) that the fresh side drops. So before #1726 a warm run could differ from a cold one.

The finished tier is written only from a fresh extraction, and a finished-tier miss re-extracts
rather than re-scoping the raw cache entry, so warm runs through `resolve_input` reproduce cold
output (a cache populated before #1726 pays one extra extraction per operand, once). The asymmetry
itself remains for any other caller that scopes a deserialized snapshot. Root cause (which state the
serialization round-trip loses or adds that scoping reads) is not yet identified.
