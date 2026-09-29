# evaluation/ — evidence about abicheck itself

Everything here measures abicheck rather than being part of it. None of it is
shipped in the package or imported by `abicheck/`.

| Directory | What it evaluates | Entry point |
|---|---|---|
| [`field/`](field/README.md) | Field benchmark against real conda-forge libraries | `python evaluation/field/runner.py` |
| [`validation/`](validation/README.md) | Real-world validation runs against upstream C/C++ libraries (false-positive catalog, example matrices) | `evaluation/validation/scripts/` |
| [`agents/`](agents/README.md) | Coding-agent behavioral tasks and the published Agent Skill evals (`agents/skills/`) | `python evaluation/agents/run_task.py`, `evaluation/agents/skills/run_skill_eval.py` |

Each subdirectory keeps its own `CLAUDE.md`/`AGENTS.md` with the scoped
instructions for that tree.
