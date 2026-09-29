### Changed

- **Repository layout** — the opt-in LibTooling companion moved from `tools/clang-layout-tool/` to `contrib/clang-layout-tool/`, next to the other Clang companion (`contrib/abicheck-clang-plugin/`); the one-off PVXS `HANDOFF.md` moved from the repository root to `docs/contribute/archive/pvxs-integration-handoff.md`. No runtime behavior changes.
- **Evaluation trees consolidated** — the three top-level evaluation directories moved under one `evaluation/` root: `eval/` → `evaluation/field/`, `validation/` → `evaluation/validation/`, `agent-evals/` (including the Agent Skill evals) → `evaluation/agents/`. Generated Harbor tasks and the skill-eval pack were regenerated for the new paths.
