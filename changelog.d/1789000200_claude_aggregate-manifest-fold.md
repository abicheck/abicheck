### Changed

- **`aggregate --run-plan` is retired; `--manifest` takes either document.**
  A run plan was a second schema for `--manifest`'s own input — the set of
  targets the matrix was supposed to produce — and was already projected to
  the manifest shape internally. `abicheck aggregate reports/ --manifest
  run-plan.json` now does what `--run-plan run-plan.json` did, with the shape
  recognized from the document's own `schema` discriminator rather than its
  filename, so either artifact can arrive under any name a CI job gives it.
  The old spelling is a usage error (exit `64`), with no hidden alias.
  `--discovered-only` is unchanged and still explicit: it states that the
  operator has no expected inventory, which no document's contents can say
  for them.
