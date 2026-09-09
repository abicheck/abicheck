<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->
### Fixed

- A directory/package `compare --contract ...` run with no severity
  configuration in effect could silently drop a finding contract
  evaluation left `NOT_EVALUATED` (unresolved/unproven/proven-out-of-
  contract relevance) from the release summary's `findings` list and from
  `--view show=...`/`--view impact` — the release fan-out's own display
  buckets only walked the compatibility-scored verdict buckets, unlike the
  equivalent scalar `compare` report (which serializes every change) or
  `scan --against` (which already surfaces its own `not_evaluated`
  bucket). Such findings now appear under their own `not_evaluated` bucket
  in the release findings list, matching the single-pair report's
  dedicated not-evaluated disclosure.
