### Fixed

- **Three documented GitHub Action examples passed inputs the Action does not
  declare.** `docs/use/severity.md` and `docs/use/github-action-recipes.md`
  both showed `severity-addition: error`, which is not an input — the
  severity page's own next paragraph says per-category overrides are not
  Action inputs — and `docs/use/fork-pr-reporting.md` used `old`/`new`/
  `headers`/`output` instead of `old-library`/`new-library`/`header`/
  `output-file`. GitHub accepts an unknown `with:` key silently, so a reader
  copying any of these got no error, just an ignored value. A new sweep
  (`tests/test_docs_action_examples.py`) checks every documented first-party
  Action step's inputs and every `steps.<id>.outputs.<name>` expression
  against the real `action.yml`, so the next rename cannot land the same way.
