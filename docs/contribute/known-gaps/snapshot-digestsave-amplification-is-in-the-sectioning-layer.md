---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Snapshot digest/save amplification is in the sectioning layer, and four attempts to reduce it bought nothing (2026-09-17)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** Records four measured digest/save reductions, three of them reverted, plus a measurement-method error. The remaining peak in canonical_form needs a storage-format ADR.


Measured while fusing `snapshot_to_dict()`'s two encoding passes into one. The
digest path peaks at roughly **16x the live model** and the encoder is not why.
Stage peaks for `_uncached_snapshot_content_digest()` over a synthetic
20,000-function snapshot whose live model is 36.35 MiB, with the source dict
already allocated and *not* counted:

| stage | peak |
|---|---|
| `snapshot_to_dict()` (after the fusing change) | 102.2 MiB |
| `to_sectioned_document(...)` | **474.0 MiB** |
| whole function | 576.1 MiB |

`to_sectioned_document` repackages the already-encoded document through
`import_legacy_snapshot` -> `InMemoryObjectStore.put`, which builds a section
DTO, a `to_dict()` of it, a `strip_capture_metadata`/`canonical_form` copy, and
a canonical-JSON string for hashing -- several more whole-document copies on
top of the one the encoder just produced. Within one `put` of the largest
section: `canonical_form` 120.3 MiB, the JSON string 46.9 MiB, its `.encode()`
44.2 MiB, `copy_of_canonical_form` (what `store.get` pays) 120.3 MiB.

**Four reductions were implemented, measured, and three reverted.** All are
recorded because each looks obviously worthwhile from the source and is not:

1. **`iterencode` into a running sha256** instead of `dumps(...).encode()` in
   `_uncached_snapshot_content_digest`. Exactly equivalent
   (`"".join(iterencode(o)) == dumps(o)`), removes a 318 MiB string and a
   396 MiB bytes copy -- and moved the measured peak *not at all* (576.1 MiB
   either way), because both allocations happen strictly after sectioning has
   already peaked above them. ~20% slower. Reverted; the reasoning is in that
   function's own docstring.
2. **The same, one level down**, inside `semantic_digest_of_canonical_form`,
   where the string is built *during* sectioning rather than after. Also no
   change (474.0 / 576.1 unchanged), also ~15% slower. Reverted.
3. **`InMemoryObjectStore.detach`** -- `get` returns a defensive deep copy, and
   `to_sectioned_document` builds a store, fills it, reads it all back, and
   discards it, so that copy guards an object about to become garbage.
   Removing it eliminates a real 120.3 MiB copy. It changed neither the peak
   (474.0 MiB before and after) nor the time (45.61 s vs 45.69 s). Reverted.
4. **`hashlib.new(algorithm, domain + payload)`** -> two `update()` calls.
   This one was **kept**: the concatenation materialises a complete second copy
   of the payload (44.2 MiB on this fixture, 0.0 MiB after) purely to prepend
   five bytes, and removing it costs nothing and adds no API. It does *not*
   move the end-to-end peak either, and is kept as waste removal rather than
   as a measured improvement.

**A measurement error worth not repeating.** Attempt 3 was first reported as
576.1 -> 474.0 MiB, an apparent 18% win. It was an artifact: the "before"
figure came from a script that allocated the source dict *after*
`tracemalloc.start()` and the "after" from one that allocated it before, so the
two differed by exactly the 102 MiB dict and not by anything the change did.
Running the *same* script against the unmodified base revision showed 474.0
both ways. Any before/after here must come from one script run against two
revisions -- a `git worktree` of the base is the cheap way -- never from two
scripts.

**What actually remains.** The peak is the DTO -> `to_dict()` ->
`canonical_form` chain, each link of which is a whole-document container tree,
and a live-allocation attribution at the end of sectioning confirms the residue
is `canonical_form`'s own rebuilding (`storage/canonical.py:198-202`). Reducing
it means changing how sections are built and addressed, and the digests
involved are persisted content addresses -- a storage-format decision needing
its own ADR and migration, not a serializer patch. ADR-063 Phase 8's
`project-snapshot-dto-no-asdict` gate already points at the same DTO files,
which is the natural home for that work.

Note this is the *digest and single-file save* path specifically. The
multi-library bundle writer does not go through it -- it encodes each member
with `snapshot_to_dict()` directly, and its own whole-bundle retention was
fixed (see `storage/bundle_facts_archive.py`). **No claim is made here that
large-library OOM is solved.**
