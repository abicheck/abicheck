### Added

- **`pr-comment-key` Action input** (#1708) — parallel jobs on one pull
  request (a matrix, one job per library) can now each keep their own sticky
  comment instead of overwriting one another. The key is folded into the
  hidden marker (`<!-- abicheck-sticky-report:<key> -->`, also
  `abicheck pr-comment --comment-key`), and `update` mode edits only the
  comment carrying the same key. Empty (the default) keeps today's single
  comment and marker unchanged. Characters outside `[A-Za-z0-9._-]` become
  `-`, so a key cannot close the HTML comment.
