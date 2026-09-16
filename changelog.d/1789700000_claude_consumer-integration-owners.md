### Added

- **Declarative component selection for `actions/baseline`.** A new
  `library-spec` input takes the libraries to dump *by pattern* rather than by
  resolved path — the same entry vocabulary as `libraries`, plus
  `header_exclude` and glob/array path fields — so an integrator declaring
  "this component owns every installed public header except the one its sibling
  owns" no longer hand-rolls `find`/`readelf` in shell.
  `abicheck.frontends.action.library_selection` picks the single real shared
  object out of a directory that also holds its SONAME symlinks (aliases are
  de-duplicated by resolved identity, never banned; only a link escaping the
  declared root, or two genuinely distinct files, is refused), checks each
  artifact is an ELF shared object and that every component names the same
  machine, and refuses a header or exclusion pattern matching nothing. Include
  roots stay parser context and never widen what a component owns.
- **`actions/resolve-baseline` gains `kind: members`**, resolving several
  targets out of one already-staged set in a single call through the identical
  per-target resolver, and reporting them on new `snapshot-paths`/`members`
  outputs. It is usable for a *candidate* set as well as a baseline, which is
  what lets a multi-component project name its snapshots without re-parsing
  `manifest.json` in shell — a parse that reliably picks up the manifest's
  `artifact` field (the producing runner's absolute binary path, meaningless
  after the set moves between jobs) instead of the portable `snapshot` entry.
- **`actions/aggregate`**, which builds `abicheck aggregate`'s reports
  directory and expected-target manifest from one declaration of the checks a
  run was supposed to produce, then validates the document it produced. It
  keeps compatibility and operational success apart: `abicheck aggregate`'s own
  exit code is reported on `compatibility-exit` and never fails the Action,
  while a refused declaration or a document that does not describe a real
  outcome always does — replacing the `aggregate … || true` plus home-grown
  JSON key check integrators were writing. A manifest or a previous run's
  `aggregate.json` inside the reports directory is refused rather than silently
  aggregated as an extra target, and a report recording its own `target_id` is
  authoritative over the declaration.
- **`actions/verify-baseline-source`**, the staging half of baseline
  resolution: which producer run a baseline may be taken from, and whether a
  name really is a release tag. Tags are resolved through `git/ref/tags/<name>`
  explicitly (a generic revision endpoint resolves a *branch*), annotated tags
  are peeled, and a name is never required to carry a `v` prefix. Producer-run
  eligibility checks repository, workflow, event, branch, exact revision,
  conclusion and named required jobs — an unrelated matrix failure is accepted
  only under an explicit policy, and a required job that never ran is reported
  separately from one that failed. `not_found` (a real lifecycle state) and
  `lookup_failed` (an operational error) are distinct outcomes, so a transient
  API error can no longer read as "no baseline, therefore compatible".
