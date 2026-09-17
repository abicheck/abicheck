### Added

- **`publish-baseline.yml` can publish a baseline-set captured by a
  different, already-completed producer run.** Its pre-captured mode could
  only take a set from its own run, which is the easy half: the uploading job
  and the publishing job share a run, so the artifact's provenance is the
  caller's own. A project whose release baselines are published by an
  automatic `workflow_run` job has no such luxury, and closed the gap by
  writing its own publisher. Setting `baseline-set-source-run-id` (with
  `baseline-set-source-repository`, `-run-attempt`, `-expect-workflow`,
  `-expect-event` and `-allowed-conclusions`) turns on real producer
  verification and takes the bytes through the API instead. A
  pull-request-triggered producer is refused unconditionally — a
  contributor's own build may not mint a release-contract baseline whatever
  else is configured — and artifacts are selected by their own ids, so the
  publication fetches the exact bytes whose origin was established rather
  than re-resolving a name pattern. A declared attempt reads that attempt's
  own document, so a re-run started after the trigger cannot be published
  under the first attempt's decision. Both acquisition modes converge on the
  existing member/schema/path/profile/generation/content validator and the
  existing immutability chain. Owner:
  `abicheck.frontends.action.precaptured_source`.
