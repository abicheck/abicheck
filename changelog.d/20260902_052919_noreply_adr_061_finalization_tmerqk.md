### Changed

- **ADR-061 file-size cleanup, batch 1.** Split two oversized `abicheck/`
  modules along their existing internal seams, verified with
  `scripts/check_architecture.py`, `mypy`, and the affected test suites:
  `type_reachability.py` (1504 → 1132 lines) gained a sibling
  `type_reachability_stdlib_spellings.py` for
  `directly_referenced_stdlib_type_spellings` and its private helpers, and
  `pr_comment.py` (1545 → 882 lines) gained `pr_comment_render.py` for its
  `CommentModel` → markdown rendering half, matching that file's own
  pre-existing "Parsing" / "Rendering" section divider. Both splits keep
  every existing import path working (`abicheck.type_reachability.
  directly_referenced_stdlib_type_spellings` and
  `abicheck.pr_comment.render_comment`/`MARKER`/`DETAIL_LEVELS`/
  `GITHUB_COMMENT_LIMIT`/`_header` all still resolve) via a re-export in the
  original module — a dynamic `importlib.import_module` indirection for
  `type_reachability.py` (its sibling needs symbols back from it, so a
  static re-export would cycle) and a plain static re-export for
  `pr_comment.py` (its rendering half needs nothing back from the parsing
  half, so no cycle risk there).
