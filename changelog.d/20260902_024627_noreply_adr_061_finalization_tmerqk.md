### Documentation

- **ADR-061 narrative brought back in sync with the tree.** The
  responsibility-package migration document (`docs/contribute/adr/
  061-responsibility-package-architecture.md`) still recorded `cli_params.py`'s
  physical move into `frontends/cli/options/` as blocked, even though the
  move (and its two prerequisite classifications — `buildsource.scan_levels`
  as `model`, and `abicheck/policies/__init__.py` as `policy`, reached
  through `workflows.policy_file.builtin_policy_names`) had already landed
  and been verified clean against `scripts/check_architecture.py`. Added the
  missing closure note so the document reflects the current tree rather than
  a stale investigation snapshot. Also removed two comments in
  `abicheck/service.py` that still described the now-removed MCP server as a
  live consumer of the typed request API.
