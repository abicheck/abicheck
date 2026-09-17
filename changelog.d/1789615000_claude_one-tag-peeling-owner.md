### Fixed

- **`actions/verify-baseline-source` no longer accepts a ref pointing at a
  tree as a release tag's commit.** `baseline_source.resolve_tag` had its own
  copy of the tag-peeling rule and was the looser of the two: it treated any
  object whose type was not `tag` as the commit, so a ref naming a tree
  resolved as a perfectly good release baseline. It now delegates to the
  shared owner (`abicheck.frontends.action.tag_resolution`), which also
  brings the near-miss-prefix and wrong-ref refusals with it. The
  `tag`/`not_a_tag`/`lookup_failed` outcome vocabulary its callers branch on
  is unchanged; the more specific reason travels on the new
  `TagResolution.refusal_code`.
