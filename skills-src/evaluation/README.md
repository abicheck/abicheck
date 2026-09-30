# skills-src/evaluation/ — evidence about abicheck and its skills

Everything here measures abicheck or its Agent Skill rather than being part
of either. None of it is shipped in the package, imported by `abicheck/`, or
published by `scripts/gen_agent_skills.py` (this directory has no `SKILL.md`).

| Directory | What it evaluates | Entry point |
|---|---|---|
| [`field/`](field/README.md) | Field benchmark against real conda-forge libraries | `python skills-src/evaluation/field/runner.py` |
| [`validation/`](validation/README.md) | Real-world validation runs against upstream C/C++ libraries (false-positive catalog, example matrices) | `skills-src/evaluation/validation/scripts/` |
| [`agents/`](agents/README.md) | Coding-agent behavioral tasks and the published Agent Skill evals (`agents/skills/`) | `python skills-src/evaluation/agents/run_task.py`, `skills-src/evaluation/agents/skills/run_skill_eval.py` |

Each subdirectory keeps its own `CLAUDE.md`/`AGENTS.md` with the scoped
instructions for that tree.
