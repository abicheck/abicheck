<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **PR-comment data model now has one owner in `report/`** — `Finding` and `CommentModel` moved from the renderer-package `pr_comment_base` into `report/pr_comment_model.py`, so the `report` package no longer imports upward into the renderer for its model. `pr_comment_base` re-exports both names, so existing imports keep working. The markdown cell sanitiser `_esc` is now the public `escape_cell` in the same module. Rendered PR-comment output is unchanged.
