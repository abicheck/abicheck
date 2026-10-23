# ADR-087: Publishing Full Reports as a GitHub Pages Site, Opt-In, From a Separate Least-Privilege Workflow

**Date:** 2026-10-23
**Status:** Proposed — not implemented. Implementation (Stage 2) waits for this ADR's acceptance by a maintainer.

Governed by: [ADR-047](047-github-actions-integration-model.md) (the Actions integration model: reusable workflows, the elevated-permission boundary, release/push-only publishing), [ADR-073](073-report-only-publication-and-the-trusted-reporter-boundary.md) (publication is a separate capability from analysis; the artifact is hostile input; a trusted reporter does not make contents trusted), [ADR-070](070-action-layer-does-not-encode-cli-semantics.md) (the Action layer does not re-implement engine semantics), [ADR-042](042-compatibility-versus-gate-separation.md) / [ADR-064](064-canonical-gate-algorithm-and-exit-decision.md) (compatibility and gate are separate from publication). Amends none of them.

## Context

Every abicheck run already produces a complete HTML report: `compare -o html=...`, the release report (`render_release_html.py`), and `project history -o html` (`render_history_html.py`). Today that HTML leaves the run only as a workflow artifact. Reviewers see a Markdown digest in the job summary and in the PR comment, and the full report is one "Download" click away, behind a GitHub login, inside a zip. The comment's footer link is the `pr-comment-report-artifact-url` input, which the Action documents as "a direct URL of an uploaded full-report artifact".

Three facts constrain the design:

1. **GitHub Pages is one site per repository.** A `deploy-pages` deployment replaces the whole site. This repository already deploys its documentation site from `.github/workflows/pages.yml` (`actions/upload-pages-artifact` + `actions/deploy-pages`, pinned by SHA). A second publisher in the same repository would silently delete the other's content. Consumers' repositories each own their own site, so the risk is per-repository, but it is real and must be stated.
2. **`deploy-pages` needs `pages: write` and `id-token: write` on the job that calls it.** A composite Action runs inside its caller's job. If the Action deployed Pages, every user of the Action would hold a Pages write grant and an OIDC token for a step most of them never asked for. That violates ADR-047's rule that the elevated-permission boundary sits in a workflow the caller opts into.
3. **Pages content is public.** The report is produced from a pull request, which may come from a fork. A fork run must never reach the deployment step, and a report must never be presented as more trustworthy than its producer made it (ADR-073 D9).

The request is for the full report to be *published* — browsable, linkable, with history — without changing what the Action *decides*.

## Decision

**Publishing a full report is an opt-in, deploy-only capability in a reusable workflow. It consumes already-produced report artifacts, runs no analysis, cannot change a verdict or an exit code, and holds Pages permissions only in the one job that deploys.**

### D1 — Form: a reusable workflow, not a composite Action and not an Action input

The entry point is `.github/workflows/publish-report.yml` (`on: workflow_call`), in the family of `publish-baseline.yml` (ADR-047 §6/§10) and `check-project.yml`. It is three jobs, each with the narrowest permission set that does its work:

| Job | Permissions | Does |
|---|---|---|
| `build-site` | `contents: read`, `actions: read` | Lists the caller's recent report artifacts and the previous successful publish run's site artifact; runs the site generator (D3); uploads the result with `actions/upload-pages-artifact`. |
| `deploy` | `pages: write`, `id-token: write`; `environment: github-pages` | Calls `actions/deploy-pages` on the built artifact and nothing else. |
| `link-report` | `pull-requests: write` | Runs only when `deploy` succeeded and the run is a PR run; writes the sticky PR comment that carries the page link (D7). |

No `pages`, `id-token` or `pull-requests: write` appears in `action.yml`. The main Action keeps its current permission requirements and gains no new ones.

A composite `actions/publish-report` is **not** chosen. The only step a composite would own is the site generator, and that is a Python command (D3) that the reusable workflow runs with one `run:` line — the same reasoning ADR-047's G30 plan gives for not adding an `actions/aggregate` composite. A composite would also hide the `pages`/`id-token` requirement inside a caller's job, which is the thing D1's job split exists to prevent.

### D2 — Mechanism: the official Pages artifact path, pinned, never a branch push

Deployment uses `actions/upload-pages-artifact` and `actions/deploy-pages`, pinned to the same full commit SHAs `pages.yml` already uses (`fc324d3547104276b827a68afc52ff2a11cc49c9` and `cd2ce8fcbc39b97be8ca5fce6e763baed58fa128`, both `# v5`). The workflow never pushes to a `gh-pages` branch and never requests `contents: write`. A re-pointed tag is a supply-chain risk on a job holding `pages: write`, so the SHA-pin rule of `publish-baseline.yml` applies unchanged.

### D3 — Site structure and run identity

The site is a directory tree generated from the report artifacts, not hand-written pages:

```text
index.html                      # run list: main, release tags, PRs — newest first
runs/main/<sha>/index.html      # one run page: links to each report it has
runs/main/<sha>/compare.html    # compare -o html
runs/main/<sha>/release.html    # release report
runs/main/<sha>/history.html    # project history -o html
runs/main/<sha>/report.json     # the JSON each HTML was rendered from, beside it
runs/tag/<tag>/<sha>/...        # release tags
runs/pr/<number>/<sha>/...      # pull requests (only when D5 allows)
```

Run path components are derived from the workflow run's own metadata (`github.ref_name`, the PR number, the commit SHA), never from file names inside a report. Each component must match `[A-Za-z0-9._-]{1,100}` and must not be `.` or `..`; a ref that fails that rule is skipped and disclosed on `index.html`, not sanitized into a colliding name. The run-page and index HTML are rendered by the generator in `abicheck/report/` (owner of "output format", ADR-061). The JSON beside each HTML is the same canonical document `compare`/`release`/`history` already write, so a reader can verify a page against its source.

Every page shows its provenance on its first line: the ref, the full commit SHA, the workflow run URL, and the time it was published. The provenance block is generated from run metadata, not from the report body.

### D4 — History survives whole-site replacement; retention is bounded and disclosed

`deploy-pages` replaces the site, so each publish must rebuild the existing runs. The chosen mechanism is **restore-from-previous-artifact**:

1. `build-site` finds the most recent *successful* publish run of this same workflow in this repository, downloads its `github-pages` artifact, and unpacks it as the base of the new site. Only the same repository's artifacts are read; no fork artifact is ever a base.
2. The new run's report is added under `runs/<kind>/<name>/<sha>/`, and `index.html` is regenerated from the *union* of the restored run pages and the new one.
3. A concurrency group serializes publishes (`group: publish-report-<repo>`, `cancel-in-progress: false`, like `pages.yml`). Serialization is what makes "the most recent successful publish run" the correct base: two concurrent publishes would each restore the same predecessor and the later deploy would drop the other's run.

Retention is bounded on two axes, both caller-configurable with defaults:

- `max-runs` (default **50**): oldest runs beyond the cap are dropped at generation time.
- `max-site-bytes` (default **512 MiB**): the GitHub Pages recommended site size is 1 GB; the default keeps headroom. Oldest runs are dropped until the total fits.

Pruning is never silent. `index.html` states how many runs were pruned, the oldest run it still holds, and why (cap reached). This is ADR-067's "100 removals detected, 100 suppressed by rule X stays visible" applied to publication history.

**Disclosed gap.** Restore depends on the predecessor's artifact still existing. Artifacts expire (the workflow's `retention-days`, default 90 in GitHub's settings). When no predecessor artifact is found, the site starts from the current run and `index.html` says "history begins at \<sha\>; earlier runs were not recoverable". The site never implies an unbroken history it cannot show.

**Alternative kept open.** A storage branch (an orphan `abicheck-report-history` branch holding the JSON) survives artifact expiry, at the cost of a `contents: write` grant in a job and unbounded repository growth. It is the fallback if restore-from-artifact proves too short-lived in practice. Question 1 in the Open questions asks the maintainer to pick.

### D5 — Which runs publish

- **Default:** runs on the default branch and on release tags only. These are the runs whose results a reader wants to find.
- **Pull requests:** off by default, enabled by an explicit `include-pull-requests: true` on the caller. Even when on, only a PR whose head repository is the base repository publishes.
- **Fork pull requests never reach `deploy`.** The `build-site` and `deploy` jobs carry the condition `github.event.pull_request.head.repo.full_name == github.repository` where the event is a PR, and the deploy job is additionally unreachable from any `pull_request` trigger (ADR-047 §12: baseline- and publication-writing workflows are push, workflow_dispatch or release triggered only). A fork's read-only token and absent OIDC token would make the deploy fail anyway; the condition exists so a fork's report never reaches a privileged job's input at all.
- A `workflow_run`-triggered caller reads the producer's artifacts, so the same-repository condition applies to the producer run's `head_repository`, not only to the publisher's own event.

### D6 — Security: the page is static, escaped and hostile-input-safe

- **No script, no external resource.** The HTML renderers that produce the report pages contain no `<script>`, `<link>` or `<img>` element (verified by grep on 2026-10-23 over `abicheck/report/`). The generator adds none. Every generated page carries `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'">`, so a future renderer regression that adds a script or a remote load is blocked by the browser, not only by review.
- **Escaping.** Every value the generator writes into HTML goes through `html.escape` (the same discipline `render_history_html.py` already follows). Report text, symbol names and ref names are all untrusted strings.
- **The artifact is hostile input (ADR-073 D8).** The restored and newly downloaded JSON is read under the same rules: size and entry caps, no paths outside the run directory, no symlinks, `json.loads` only. A file that fails is refused and listed on `index.html` as refused, never silently dropped.
- **Contents are not promoted (ADR-073 D9).** Publishing does not raise a report's assurance. The page shows the report's own recorded fields (confidence, evidence tier, coverage warnings, the scope-completeness block) exactly as recorded; the provenance line says which run produced it. A same-repository contributor can still publish a report whose contents they chose, and the site says so by showing provenance rather than by asserting trust.

### D7 — Where the link goes: the existing PR comment, updated after deploy succeeds

A link must not point at a page that does not exist. The Action writes its PR comment during the check run, before any deployment, so the check run cannot carry a working page link.

- The **job summary** of the check run is unchanged: it stays Markdown and carries no page link.
- The **publisher's own job summary** (in `publish-report.yml`) names the page URL, written only after `deploy` succeeded.
- The **PR comment** carries the page link through the sticky identity ADR-073 D4 defines. The `link-report` job updates that comment after `deploy` succeeds. If `deploy` fails, the comment is left as the check run wrote it, and `link-report` does not run, so no link is written that does not work. A failed deploy never produces a comment that claims a page exists.

**Input naming.** The Action's existing `pr-comment-report-artifact-url` input is documented as "a direct URL of an uploaded full-report **artifact**". A Pages URL is not an artifact URL, and reusing the input would make its documented contract false for every caller that already sets it. This ADR therefore proposes a sibling input, `pr-comment-report-page-url`, and does not overload the existing one. The request this ADR answers asked to reuse "the existing input"; that is the one place the design departs from the request, and it is listed in Open questions (Q3) for the maintainer to confirm. Adding the input is a public-surface change, so Stage 2 must follow ADR-068/quality-gates' surface-traceability rule and the Action/CLI drift gate.

### D8 — Publication never changes the verdict, the exit code or the gate

- The publisher is a separate workflow. Its jobs are not `needs` of the check workflow, and the check workflow's conclusion does not depend on it. A failed publish fails the publish workflow's own check, with its own message.
- A publish failure is never reported as a clean compatibility result, and never downgrades or hides one. A run whose report was not published says so on the PR comment as "page not published: \<reason\>" only if the PR comment is being rewritten; it is never shown as a pass (ADR-073 D3).
- A non-clean verdict never fails the publisher. The gate was already decided in the producer job (ADR-042, ADR-064), and the exit code of `compare`, `release` and `project history` is unchanged by this ADR.
- No exit code, schema or CLI flag changes in Stage 1 or Stage 2. Stage 2 adds only the generator's own command surface, which is internal to the workflow.

### D9 — One Pages site per repository, owned by one publisher

Enabling `publish-report.yml` makes that repository's Pages site contain **only** the report site. The caller must not also run another `deploy-pages` job there. This repository is the case in point: its `pages.yml` owns the documentation site, so `publish-report.yml` must not be enabled on `abicheck/abicheck` itself. The workflow's documentation states this as a prerequisite, and Stage 2's contract test asserts that the reusable workflow's only `deploy-pages` call is in its `deploy` job.

## Consequences

**Positive**

- A reviewer can open the full HTML report from the PR comment, and history is browsable without downloading a zip.
- No new permission is granted to a user of the Action, and no branch is written.
- The publication decision is testable without Pages: the generator is pure Python over artifacts, and the workflow can be exercised in `test-action.yml` by building the site as an artifact and stopping before `deploy`.

**Negative / cost**

- Three jobs and a restore step are more moving parts than one Action step. The restore step depends on artifact retention; its failure mode is a disclosed truncated history, not a silent one.
- A repository's Pages site is shared with nothing else once this is on; that is a real operational constraint for repositories that also publish docs.
- The page-link input is a new public input (Q3), and Stage 2 owes the surface-traceability entry for it.
- Retention defaults (50 runs, 512 MiB) are a judgment; they are configurable and disclosed, but they will need revisiting against real report sizes.

## Alternatives considered

1. **Keep artifacts only (status quo).** Zero new surface. Rejected as the end state: it is the thing the request asks to improve. It remains the fallback when a caller does not opt in.
2. **Publish to a `gh-pages` branch.** The classic approach. Rejected: it needs `contents: write`, makes the branch the source of truth for a generated site, and is the write path the request ruled out.
3. **Deploy from the check workflow directly.** Rejected: it puts `pages: write` and `id-token: write` in the caller's check job, and it couples the publication outcome to the gate's job.
4. **Composite `actions/publish-report`.** Rejected (D1): a composite inherits its caller's job permissions, so deploying from one forces the grant onto the user's job.
5. **Third-party static hosting (S3, Netlify, an external viewer).** Rejected: it adds a credential, a second trust boundary and an external service for content that already has a home on the repository's own Pages.
6. **Storage branch as the history store, from the start.** Deferred, not rejected (D4). It is the durable option if artifact expiry is too short; it costs `contents: write` and repository growth that a first version should not take on by default.
7. **Publish PR runs by default.** Rejected (D5): it would put every PR's HTML on a public site by default, including PRs from people who have not been asked to be published.

## Open questions (decided by the maintainer before Stage 2)

- **Q1 — History store.** Restore from the previous publish run's Pages artifact (D4 default), or a storage branch from the start?
- **Q2 — Retention defaults.** `max-runs: 50` and `max-site-bytes: 512 MiB` are proposals. Confirm, or give the expected report size so the defaults can be checked against it.
- **Q3 — Link input.** A new `pr-comment-report-page-url` input (D7 recommendation), or reuse `pr-comment-report-artifact-url` as the request asked?
- **Q4 — PR runs.** Confirm that pull requests are opt-in (`include-pull-requests`, default off), not on by default.
