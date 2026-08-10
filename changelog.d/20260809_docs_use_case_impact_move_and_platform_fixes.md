### Fixed

- **A docstring in `abicheck/impact/use_cases.py` (and its test) pointed at
  the pre-move `docs/use/use-case-impact.md` path.** The guide moved to
  `docs/contribute/use-case-impact.md` (it documents a library-only,
  Python-API-only preview capability, not yet a supported CLI/report
  workflow); the docstring reference is updated to match, so the public
  API's own pointer to its documentation doesn't dangle.
