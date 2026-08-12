<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Documentation

- Fixed a YAML folded-scalar line break inside a filename token in
  `action.yml`'s `abi-baseline` input description (rendered as a spurious
  inserted space in generated docs) and regenerated
  `docs/reference/github-action-inputs.md` from it.
- Updated `docs/reference/resolve-baseline.md`'s worked examples to pin
  the current commit SHA (the previous placeholder predated
  `expected-project-ref` support).
- Linked `docs/reference/publish-baseline.md`'s `actions/stage-baseline`
  section back to `docs/use/baseline-storage.md`, its registered
  canonical owner, and fixed a stale `tar --zstd` mention in the
  `snapshot-compression` input's description.
