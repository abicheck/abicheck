<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G38 bundle analysis: signature-change promotion now checks every
  versioned definition a provider exports for a symbol, not just the
  first one found.** `consumer_resolves_via_provider` used `next()` to
  pick a single `ProviderEntry` for the candidate provider — but a
  library can legitimately export multiple versioned definitions of one
  bare symbol (the compat-symbol pattern `foo@V1` alongside `foo@@V2`).
  Picking only the first entry (e.g. a non-default `V1`) could test it
  against a consumer explicitly requiring `V2` and wrongly conclude no
  match, silently dropping a promoted finding even though the same
  provider's `V2` entry does match. Fixed by checking `any()` over every
  entry from that provider, mirroring `_detect_unresolved_intra_
  dependency`'s own matching.
