# AGENTS.md — `abicheck/storage/`

## Purpose

This package owns snapshot/baseline serialization formats, storage-level
caching behavior, and their schemas/migrations, per ADR-061 D1. It answers
"how are already-computed facts written to and read from disk" -- never
"what those facts mean" or "whether a comparison is valid."

## Permitted imports

Per ADR-061 D1, `storage/` may depend only on `model`. In practice, today,
this package's two modules (`bundle_archive.py`, `bundle_archive_cd_guard.py`
-- the latter split out of the former purely to stay under the 800-line
production cap, see its own docstring) import only `abicheck.errors`
(`SnapshotError`, the project-wide error vocabulary) and no `model`/
`compare` type at all -- see `bundle_archive.py`'s own docstring for why:
the `BundleFacts`/`AbiSnapshot`-aware glue that would need a `model` import
stays in `bundle_facts.py`/`serialization.py` (still flat-root, not yet
part of this migration) rather than being pulled into this package
prematurely, since `bundle_facts.py` itself cannot yet join `model`
cleanly (a pre-existing `TYPE_CHECKING`-only coupling to
`checker_types.DiffResult` would create a `model -> compare -> model`
cycle -- confirmed by running `scripts/check_architecture.py`, not
assumed). A future module added here that genuinely needs a `model`-owned
type should import it directly once that type has actually joined `model`
-- not via `serialization.py`.

## Canonical entry points

`bundle_archive.py`'s `BundleArchiveWriter`/`BundleArchiveReader` are a
pure, content-addressed zip-container primitive (G40) -- write/read a
manifest plus content-hash-addressed blobs, nothing more.
`bundle_archive_cd_guard.py`'s `reject_absurd_central_directory` is its own
central-directory bomb guard, called from `BundleArchiveReader.__init__`
before `zipfile.ZipFile` ever parses the archive. Callers that want a real
`BundleFacts` written to or read from one of these archives go through
`serialization.py`'s `save_bundle_facts`/`load_bundle_facts`
(`format="archive"`), which delegates the actual glue to `bundle_facts.py`
(still flat-root), the module that already owns the `BundleFacts`-to-dict
conversion this format's blobs are built from.

## Tests

`tests/test_bundle_archive.py`.

## Prohibited responsibilities

This package must not parse a binary, run a comparison, evaluate policy, or
know what an `AbiSnapshot`'s fields mean -- it stores and retrieves bytes a
caller already produced, addressed by content hash. A caller needing this
package to interpret its own payload is a sign the interpretation belongs
in the calling layer, not here.
