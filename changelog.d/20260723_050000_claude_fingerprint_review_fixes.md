<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`profile_fingerprint`'s system/toolchain bucket hashed raw absolute
  paths** (Codex review, PR #624): an unattributed depfile entry (e.g. an
  auto-injected sysroot/`-isystem` header not under any declared `-I`
  directory) fed its full resolved path into the digest. Two otherwise
  identical toolchains whose system headers were materialized under
  different checkout/cache roots (`/tmp/old-sysroot/usr/include/stddef.h`
  vs. `/tmp/new-sysroot/usr/include/stddef.h`) therefore produced different
  `profile_fingerprint`s and spuriously raised `ProfileMismatchError`. The
  system bucket now hashes content only, matching the checkout-root
  independence every other part of the algorithm already provides.
- **`compute_extraction_contract` could crash on `os.path.commonpath`**
  (CodeRabbit review, PR #624): both the scope-identity root computation and
  `_header_identities` called `commonpath` unguarded, which raises
  `ValueError` when its candidates share no common anchor at all (mixed
  drives on Windows, or a local vs. UNC root). Both call sites now fall back
  to a drive-stripped, still-deterministic identity instead of propagating
  an unhandled crash out of fingerprinting.
- **A diagnostic-mode comparability mismatch discarded its `reason`**
  (CodeRabbit review, PR #624): `compare(..., diagnostic_comparison=True)`
  downgraded a genuine contract mismatch to `assurance == "none"` but
  dropped `ComparabilityMismatch.reason` entirely, leaving a report consumer
  with no explanation of which axis mismatched — undermining the escape
  hatch's stated purpose ("the caller can still see a result but knows not
  to trust it"). The reason now flows into `DiffResult.coverage_warnings`,
  the existing human-readable-gap disclosure field.
- **`DiffResult.contract_coverage`/`.assurance` typed as bare `str | None`**
  (CodeRabbit review, PR #624): each has exactly one recognized non-`None`
  value ("partial" and "none" respectively); tightened to
  `Literal["partial"] | None` / `Literal["none"] | None` so mypy/static
  consumers get that guarantee instead of an unconstrained string.
