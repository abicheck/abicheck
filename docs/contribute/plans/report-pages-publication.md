---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# Report publication to GitHub Pages — staged plan

**Status:** Proposed — Stage 1 (this document and [ADR-087](../adr/087-report-pages-publication.md)) is docs-only. Stage 2 is blocked until a maintainer accepts ADR-087 and answers its open questions Q1–Q4. Nothing in this plan is implemented.

**Owning decision:** [ADR-087](../adr/087-report-pages-publication.md). This plan sequences the work; the ADR owns the design. If the two disagree, the ADR wins and this plan must be corrected.

**Governing ADRs:** [ADR-047](../adr/047-github-actions-integration-model.md) (reusable workflows, elevated-permission boundary), [ADR-073](../adr/073-report-only-publication-and-the-trusted-reporter-boundary.md) (publication-only capability; hostile-artifact rules; sticky identity), [ADR-070](../adr/070-action-layer-does-not-encode-cli-semantics.md).

## Problem

The full HTML report (`compare -o html=…`, the release HTML, `project history -o html`) reaches a reviewer only as a workflow artifact. The job summary and the PR comment carry Markdown, and the PR comment's "Download full report" link points at a zip. There is no browsable, linkable, history-bearing view.

## Target state (what Stage 2 delivers)

- `.github/workflows/publish-report.yml` — opt-in reusable workflow (`workflow_call`), three jobs: `build-site`, `deploy`, `link-report` (ADR-087 D1).
- `abicheck/report/` — a pure-Python site generator: index and run pages, retention and pruning, restore-merge, escaping, the CSP meta tag (ADR-087 D3–D4, D6). No network, no credentials.
- `pr-comment-report-page-url` on the Action, if Q3 is answered "sibling input" (ADR-087 D7).
- A reference page, a scenario page, and an integration example under `docs/`, registered in `docs/_meta/topics.yaml` (docs/AGENTS.md: a new public surface registers its topic in the same PR).
- ADR surface-traceability entries (quality-gates.md) for the new workflow and input.
- A changelog fragment (`scriv create`).

Explicitly **not** in scope: any change to the Action's verdict, exit codes, `compare`/`release`/`project history` output content, or the report schemas.

## Stage 1 — ADR and plan (this PR, docs-only)

- [x] ADR-087 written, status Proposed, indexed in `docs/contribute/adr/index.md`.
- [x] This plan written and indexed in `docs/contribute/plans/index.md`.
- [ ] `mkdocs build --strict` passes.
- [ ] `python scripts/check_ai_readiness.py` passes.
- [ ] PR opened as ready-for-review, labelled as waiting on ADR acceptance; **no code**.

**Exit criterion:** a maintainer has accepted ADR-087 (or amended it) and answered Q1–Q4 in the ADR's Open questions section.

## Stage 2 — implementation (after acceptance)

Each step is a separate, reviewable slice. Later slices assume earlier ones merged.

### 2.1 Site generator (`abicheck/report/`)

- Pure functions: given a list of run descriptors (kind, name, sha, provenance, report files) and a restored site tree, produce the new tree. Testable with no Pages, no network.
- Path rule: each component `[A-Za-z0-9._-]{1,100}`, not `.`/`..`; a violating ref is skipped and listed, never sanitized into a collision.
- Escaping through `html.escape` for every interpolated value; the CSP meta tag on every page.
- Retention: `max-runs` (default 50) and `max-site-bytes` (default 512 MiB), oldest-first pruning, pruned count and oldest-kept run stated on `index.html`.
- Restore-merge: union of restored and new runs; a disclosed "history begins at …" line when no predecessor was found.
- Hostile-input reads: ADR-073 D8 caps and refusal rules; a refused file is listed on `index.html`.

**Tests (this slice):** index ordering; escaping of hostile ref/symbol strings (oracle: a literal expected string, not the generator's own helper); path-component rejection including `..` and `/`; pruning by count and by bytes, with the disclosed count asserted; restore with no predecessor; restore with a predecessor and a new run (union, no drop); a fork-origin descriptor is rejected before generation. Placement: `abicheck/report/` owner per ADR-061; a changelog fragment.

### 2.2 Reusable workflow (`.github/workflows/publish-report.yml`)

- Three jobs with the permission split in ADR-087 D1; `deploy` is the only job with `pages: write` / `id-token: write`; `link-report` is the only job with `pull-requests: write`.
- Pins: `actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9 # v5`, `actions/deploy-pages@cd2ce8fcbc39b97be8ca5fce6e763baed58fa128 # v5`, and the checkout/setup-python SHAs already used in `pages.yml`. No floating tags.
- `concurrency: group: publish-report-<repo>`, `cancel-in-progress: false`.
- Same-repository condition on PR runs; `include-pull-requests` default `false`.
- Predecessor lookup: most recent successful run of this workflow in this repository; its `github-pages` artifact is the base. Missing predecessor → disclosed fresh start.

**Tests (this slice):** a static contract test (in the style of `tests/test_action_report_contract.py`) asserting that the only `deploy-pages` call sits in `deploy`, that `pages: write` appears only there, that no `contents: write` appears anywhere, that every third-party `uses:` is SHA-pinned, and that no trigger is `pull_request`/`pull_request_target`.

### 2.3 Action input (only if Q3 = sibling input)

- `pr-comment-report-page-url` in `action.yml`, documented as a Pages URL, distinct from `pr-comment-report-artifact-url`, and rendered in the comment only when set.
- Regenerate `docs/reference/github-action-inputs.md` (`scripts/gen_action_reference.py`).
- Surface traceability entry in `docs/contribute/agent-guide/quality-gates.md` and the Action/CLI drift gate (`action-cli-surface-drift.md`).
- Docs topic registered in `docs/_meta/topics.yaml`.

### 2.4 Docs

- `docs/use/` task page: enabling publication on a consumer repository, including the prerequisite that Pages is set to "GitHub Actions" and that the repository's Pages site is owned by this workflow (ADR-087 D9).
- `docs/integration/` example workflow calling `publish-report.yml` after a `check-single.yml`/`check-project.yml` run.
- Scenario page under the scenario-first IA (G30 P1.7), linked, not duplicated.
- `docs/reference/` entry for the workflow's inputs and outputs, registered as a `reference_page`.

### 2.5 Verification without a real deploy

- `test-action.yml` gains a job that runs the generator over a fixture set of report artifacts and uploads the built site as a workflow artifact. It stops before `deploy`. This proves the site builds and the index is correct without touching Pages.
- A local render check: open the generated `index.html` and one run page in a headless browser, confirming no console errors and no network requests (the CSP would block them; the test asserts the header is present).

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| A consumer enables this on a repository whose Pages site already holds docs | ADR-087 D9 prerequisite stated in the task page; the contract test forbids a second `deploy-pages` in the same workflow; the risk is named, not engineered away. |
| Predecessor artifact expired, so history silently truncates | Disclosed on `index.html` ("history begins at …"). Q1 may move history to a storage branch if this proves too short. |
| Concurrent publishes drop a run | Serialized concurrency group; restore reads the most recent *successful* predecessor only after the group is acquired. |
| Fork content reaches a privileged job | Same-repository condition on every PR path; `pull_request` never triggers deploy; the artifact's head repository is checked as well, for `workflow_run` callers. |
| Link in a PR comment points at a page that never deployed | `link-report` runs only after `deploy` succeeds; a failed deploy leaves the check's comment untouched. |
| A renderer change adds a script or remote load | Browser-enforced CSP on every generated page, plus the grep-based no-script assertion over `abicheck/report/` in the test suite. |
| Report size grows past the Pages limit | `max-site-bytes` cap with disclosed pruning; default reviewed against real report sizes when Q2 is answered. |

## Decisions needed from a maintainer

These are recorded in ADR-087's Open questions. Stage 2 cannot start without them:

1. **Q1** — history store: previous publish run's artifact, or a storage branch?
2. **Q2** — retention defaults: 50 runs and 512 MiB, or other values?
3. **Q3** — link input: new `pr-comment-report-page-url`, or reuse `pr-comment-report-artifact-url` as requested?
4. **Q4** — pull-request publication: opt-in (proposed) or default?
