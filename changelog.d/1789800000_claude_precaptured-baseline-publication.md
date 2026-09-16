### Added

- **Publish an already-captured baseline-set (`publish-baseline.yml`).**
  The reusable publication workflow's new `baseline-set-artifact-prefix`
  input publishes a finished baseline-set — downloaded as an artifact, so
  it crosses a real job boundary — instead of capturing one from a
  `build-output.json`. In that mode the workflow runs no dump, no build
  query, no compiler and no comparison: it validates the set, packages it
  and publishes it through the same staging, immutability, project-ref,
  fact-set, generation and schema chain the capture path already uses. It
  is opt-in and mutually exclusive with capture mode, which is a hard
  failure rather than a resolution by step order. Closes the gap that left
  a downstream project maintaining its own publisher and its own manifest
  interpreter.

- **`abicheck.buildsource.baseline_precaptured`** — the producer-side
  mirror of what a consumer's resolver will later demand of the published
  asset: understandable manifest and snapshot schema, the profile, capture
  revision and generation the set is being published *as*, portable
  in-tree member paths (no traversal, no symlink, no unextractable
  character), and every declared digest recomputed from the bytes on disk
  rather than read off the manifest. A set this refuses is one that would
  be rejected at resolution time — which, on an immutable channel, is too
  late to be useful. Importable and credential-free, so the whole gate is
  unit-tested with no runner.

### Fixed

- **`actions/report` forwards the producing run's identity.** The Action
  declared `source-run-id`/`source-run-attempt` and `run.sh` read the
  matching `INPUT_*` variables, but no step's `env:` mapped one to the
  other, so a `workflow_run` publisher silently recorded its *own* run
  coordinates in the sticky comment's ordering marker. That inverts the
  guard: a re-run of an older commit is triggered later, so its publisher
  carried the larger id and could overwrite a newer result. The fallback to
  ambient coordinates remains, and remains correct only for the documented
  same-run case.
