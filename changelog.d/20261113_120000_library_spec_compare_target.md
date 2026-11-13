### Added

- **One library spec for baseline and gate** — the root Action's new
  `library-spec` and `library-spec-target` inputs resolve one component of
  the same declarative spec `actions/baseline` dumps from into `new-library`,
  `new-header` and `new-include`, so a multi-library project lists each
  library's headers once instead of once per workflow. `resolve-libraries`
  gains `--target NAME`. See `docs/use/multi-component-ci.md`.
