# Changelog fragments

Every PR that changes `abicheck`'s user-facing behavior adds one small file
here instead of editing `CHANGELOG.md` directly. Hand-editing the shared
`## [Unreleased]` section was causing near-constant merge conflicts — most
PRs touched the same few lines at the top of that file. This directory is
managed by [scriv](https://scriv.readthedocs.io/); see `[tool.scriv]` in
`pyproject.toml` for the configuration.

## Adding an entry

```bash
pip install -e ".[dev]"     # installs scriv
scriv create
```

This writes `changelog.d/<timestamp>_<you>_<branch>.md` from
`fragment_template.md.j2`. Uncomment exactly one `### <Category>` section
and replace the example bullet with your entry, written the way it should
read in `CHANGELOG.md`: a bold lead-in phrase, full sentences, backticked
identifiers — match the style of existing entries there. Delete the other
commented-out sections. Categories (mirrors Keep a Changelog, plus two the
project already uses):

- `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security` — standard
  Keep a Changelog categories
- `Performance` — perf-only changes with no behavior change
- `Documentation` — docs-only changes worth surfacing in the changelog

Commit the fragment file alongside your code change in the same PR.
`.github/workflows/changelog-check.yml` fails a PR that touches
`abicheck/**/*.py` without one — add the `skip-changelog` label if the
change has no user-facing effect (internal refactor, test-only PR).

## Releasing

At release time the maintainer runs:

```bash
scriv collect --version X.Y.Z
```

which reads every fragment in this directory, renders them into one
`## [X.Y.Z] — YYYY-MM-DD` section inserted at the `scriv-insert-here`
marker in `CHANGELOG.md` (just below `## [Unreleased]`), and deletes the
fragments it consumed.
