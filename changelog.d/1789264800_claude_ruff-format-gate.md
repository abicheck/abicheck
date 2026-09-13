### Fixed

- **`ruff format` now gates CI, and the tree is formatted.** The `fmt-check`
  step had been present in `scripts/verify.py`'s `fast`/`pr`/`full` profiles
  and in `pre-commit` for months, but no CI job invoked it — `ci.yml`'s
  `lint-and-types` ran `--only lint,typecheck,docs-build`, no workflow ran the
  full `pr` profile, and `pre-commit` does not run in CI at all. The formatter
  had therefore never been applied repo-wide, and the drift was growing (486
  unformatted files on 2026-09-11, 625 two days later). This applies
  `ruff format` to `abicheck/` and `tests/` (625 files) and adds `fmt-check`
  to the `lint-and-types` job, so a green CI run now says something about
  formatting. No behavior change: the reformat was verified semantics-
  preserving by comparing the parsed AST of every changed file against its
  previous version. `abicheck/model/change_catalog/kinds.pyi` is excluded from
  formatting in `ruff.toml`, because `scripts/gen_changekind_stub.py --check`
  is its formatting authority and the two gates cannot otherwise both pass.
