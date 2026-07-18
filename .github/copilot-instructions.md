<!--
  This is a thin adapter, not the canonical source (CLAUDE.md "M1-1").
  Canonical, vendor-neutral repository instructions live in /AGENTS.md —
  read that file for the architecture map, ChangeKind conventions,
  test-quality gates, and the full "what NOT to do" list. Keep this file
  short: only the minimum a Copilot session needs before it opens AGENTS.md.
-->

# Copilot instructions — abicheck

Read `/AGENTS.md` first — it is the canonical repository contract. This file
only orients Copilot to that fact and to the one command that matters before
proposing a change is done: `/scripts/verify.py`.

```bash
pip install -e ".[dev]"                          # dev install
python scripts/verify.py --profile fast           # inner loop: lint, format, types, fast tests
python scripts/verify.py --profile pr              # what CI actually requires before merge
```

`pixi run check` runs the exact same `--profile pr` command — treat either as
the definition of done, not the fast-lane command alone.

Do not duplicate commands, invariants, or counts from `AGENTS.md` into this
file — if something here and `AGENTS.md` disagree, `AGENTS.md` is correct and
this file is stale.
