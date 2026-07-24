# CLAUDE.md — `docs/`

Canonical, vendor-neutral documentation-authoring instructions live in
**`AGENTS.md`** in this directory — imported below so Claude Code loads the
full contract automatically as part of this file, mirroring the repo-root
`CLAUDE.md`/`AGENTS.md` split ("M1-1"). If you're changing a documentation
rule, edit `docs/AGENTS.md`, not this file.

@AGENTS.md

## Claude Code-specific notes

- Regenerate generated docs after the relevant source changes, then commit
  the results — see `docs/AGENTS.md`'s "Regenerating generated docs" section
  for the exact commands (`gen_examples_docs.py`, `gen_detector_spec.py`).
- Before adding a new page, check `docs/_meta/topics.yaml` for an existing
  canonical owner of the topic — `docs/AGENTS.md`'s "When does a new fact
  need a new page?" section has the decision rule.
