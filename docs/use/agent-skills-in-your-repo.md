---
doc_type: how-to
audience:
  - library-maintainer
  - ci-owner
level: intermediate
lifecycle: active
generated: false
---

# Enabling abicheck skills in your repository

[Agent Skills](agent-skills.md) describes what the abicheck skills do. This
page is for **maintainers of a C/C++ library** who want every contributor's
coding agent (Claude Code, GitHub Copilot, OpenAI Codex, Cursor, Gemini CLI)
to pick those skills up automatically when someone working in *their*
repository asks "will this break our users?" — without each contributor
discovering and installing them by hand.

The short version:

1. Install the skills **into the repository** and commit them.
2. Make sure the `abicheck` CLI is available wherever agents run.
3. Add a few lines to your `AGENTS.md` / `CLAUDE.md` pointing agents at them.
4. Optionally, let the CI skill wire a real compatibility gate into GitHub Actions.

## Which skills to enable

| Skill | Enable it when… |
|---|---|
| `check-abi-compatibility` | Your repository ships a shared library whose consumers you don't rebuild. This is the one most library repositories want: reviewers and agents get a verdict backed by a real comparison instead of a reading of the diff. |
| `explain-abi-change` | Your repository *consumes* shared libraries (an application, a plugin host, a distribution recipe) and developers hit `undefined symbol`, `version ... not found`, or post-upgrade crashes. |
| `set-up-abi-compatibility-ci` | You don't have an ABI gate in CI yet, or have one that never fails. Usually run once; keeping it installed lets a later contributor repair the workflow. |

All three are **preview** — see the evaluation status on
[Agent Skills](agent-skills.md). They are safe to enable: each one refuses to
state a compatibility verdict without a comparison it actually ran, and never
changes your environment or widens a suppression on its own (the rules ship
inside every skill as `references/shared/safety-invariants.md`).

## Step 1 — install the skills into the repository

From the repository root:

```bash
npx skills add abicheck/abicheck                              # all three skills
npx skills add abicheck/abicheck -s check-abi-compatibility    # just one
npx skills add abicheck/abicheck -l                           # list what is offered
```

Without `-g` the [`skills` CLI](https://www.npmjs.com/package/skills)
installs into the *project*, into the directory each agent reads (for
example `.claude/skills/` for Claude Code, `.agents/skills/` for Codex and
other agents that follow the portable layout). Each installed skill is a
self-contained directory — no symlinks back to anything outside it.

**Commit the installed directories.** That is what turns a per-developer
setup into a repository setting:

- every contributor, and every cloud or CI agent that clones the repository,
  gets the same skills with zero setup;
- the exact skill text is pinned by your own git history, so an upstream
  update never changes agent behavior in your repository silently;
- updates arrive as an ordinary diff you can review (see
  [Keeping them current](#keeping-them-current)).

Skills are executable instructions. Read what you commit, the same as any
other third-party code you vendor.

If you'd rather not commit them, document the one-line install in your
`CONTRIBUTING.md` instead — but then each developer's copy can differ, and
cloud agents won't have the skills unless their setup script installs them.

## Step 2 — make the `abicheck` CLI available

The skills drive the abicheck CLI through the shell; they do not bundle it.
Each skill checks `abicheck --version` against its declared range (currently
`>=0.6.0,<0.7.0`) and declines to run rather than failing halfway.

- **Developers:** add `abicheck` to your dev requirements, or document
  `pipx install abicheck` in `CONTRIBUTING.md`. Pin a version inside the
  skills' range.
- **pixi / conda environments:** add `abicheck` to the dev feature so
  `pixi install` provides it.
- **Cloud agents** (Claude Code on the web, Copilot coding agent, Codex
  cloud): install it in the environment's setup script or session-start
  hook, e.g. `pip install "abicheck>=0.6,<0.7"`. An agent that has the skill
  but not the CLI will correctly refuse to give a verdict — which is safe,
  but not useful.
- **Header-level analysis** needs `castxml` (or clang) and your library's
  build configuration; see [evidence and build-context flags](dump-compare-flags.md).
  Without them the skill still works on binaries alone, and says the
  conclusion is narrower.

## Step 3 — point agents at the skills

Skills trigger from their own description, so this step is optional, but a
short note in your repository's agent instructions makes the behavior
predictable and tells the agent project-specific facts it would otherwise
have to rediscover. Add something like this to `AGENTS.md` (and reference it
from `CLAUDE.md` / `.github/copilot-instructions.md` if you keep those):

```markdown
## ABI/API compatibility

This repository ships `libfoo.so`, consumed by applications we do not rebuild.
Any change to `include/foo/` or to exported symbols must keep binary
compatibility within a major version.

- To review a change for compatibility, use the `check-abi-compatibility`
  skill. Do not claim a change is ABI-safe from reading the diff alone.
- Public headers: `include/foo/`. Build: `cmake -B build && cmake --build build`.
  The library is `build/libfoo.so`.
- Baseline: the latest release's snapshot, `abi/libfoo-<version>.abi.json`
  (or: the merge base with `main`, built the same way).
- Intentional breaks are recorded in `.abicheck.yml`; never add a suppression
  to make a check pass — raise it with a maintainer.
```

The facts worth stating are exactly the ones a skill otherwise has to infer:
which headers are public, how to build the library, and what the baseline
is. Getting those right is most of the difference between a precise verdict
and a noisy one.

## Step 4 — add a CI gate (optional)

A skill helps whoever is talking to an agent; a CI gate checks every pull
request. They complement each other. Ask an agent with the
`set-up-abi-compatibility-ci` skill to "set up ABI compatibility checks for
this library in GitHub Actions". It inspects the build, public headers, and
release process, picks a baseline strategy, and writes a pinned,
least-privilege workflow with a staged (report-only first) rollout and a
setup report explaining each choice. Review the generated workflow like any
other PR. To do the same by hand, see [GitHub Action](github-action.md) and
[CI gating](ci-gating.md).

## Keeping them current

```bash
npx skills update         # refresh installed skills from their source
git diff                  # review what changed in the skill text
```

Commit the update as its own PR (for example `chore: update abicheck agent
skills`), and bump your pinned `abicheck` version in the same PR when a
skill's declared `abicheck-version-range` changes — the two move together.

## Checklist

- [ ] Skills installed in the project (not only globally) and committed.
- [ ] `abicheck` in dev requirements / environment, inside the skills' version range.
- [ ] Cloud-agent setup script installs `abicheck`.
- [ ] `AGENTS.md` names the public headers, build command, and baseline.
- [ ] (Optional) CI gate in place, via the CI skill or by hand.
- [ ] Skill updates reviewed as ordinary PRs.
