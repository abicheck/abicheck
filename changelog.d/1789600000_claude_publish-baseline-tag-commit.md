### Fixed

- **`publish-baseline.yml`'s pre-captured path no longer compares a release
  tag against a capture's commit.** A baseline-set records the revision it
  actually built, while publication targets a tag such as `1.5.2`, so the old
  direct `project_ref`-vs-`release-tag` equality rejected every set captured
  by a producer rather than by this workflow. The publication tag and the
  expected captured revision are now separate values: the tag is resolved
  through `refs/tags/<tag>` and an annotated tag is peeled to its commit, with
  a prefix match on a longer tag name and a branch of the same name both
  refused. The new `expected-project-ref` input states which revision to
  expect — `commit` (the default), `tag` for the documented legacy tag-valued
  behavior, or an explicit full SHA — as a closed set, never as a fallback
  from a failed comparison. Owner:
  `abicheck.frontends.action.tag_resolution`.
