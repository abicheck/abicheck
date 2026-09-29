# AGENTS.md — `packages/abicheck-skills/`

The npm package behind `npx abicheck-skills` (ADR-058's publication step).
See the repository root `/AGENTS.md` for the project-wide contract.

- `bin/abicheck-skills.mjs` — the installer. Zero dependencies, Node >= 18.
  It copies the staged skill into `.claude/skills/`, `.agents/skills/` or
  `.gemini/skills/`, writes a `.abicheck-skill.json` ownership record, and
  never replaces a directory without that record unless `--force` is given.
- `skills/` and `LICENSE` — **build output, gitignored.**
  `python scripts/build_npm_skill_package.py` stages them from `skills-src/`
  through `gen_agent_skills.render_all`, which is the only renderer. It also
  syncs `package.json`'s version to `pyproject.toml`. Never hand-edit
  `skills/`; edit `skills-src/`.
- `test/*.test.mjs` — installer unit tests (`npm test`).
  `tests/test_npm_skill_package.py` runs them from the Python suite. It also
  stages, `npm pack`s and `npx`-installs the real package, then compares the
  installed tree byte for byte against the renderer.
- Publishing: `.github/workflows/publish-skills-npm.yml`, on a GitHub
  release, with the same version as the PyPI release.
  `verify.py --only npm-skill-package` gates version drift on every PR.
