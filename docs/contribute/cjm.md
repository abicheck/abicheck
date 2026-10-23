---
doc_type: contributor
audience:
  - contributor
level: intermediate
depends_on:
  - docs/contribute/usecase-registry.yaml
lifecycle: active
generated: false
---

# Customer Journey Map (CJM)

This page maps abicheck's use cases onto the journey a library maintainer goes
through. The [use-case registry](usecase-registry.yaml) says *what* abicheck
supports. This map says *where in the user's work* each case is used, and so
shows which stages are well served and which are thin.

Each registry entry carries `journey_stages: [...]` using the stage keys below.
`tests/test_usecase_registry.py` checks that every entry has a known stage,
that every stage has at least one use case, and that this page defines exactly
the stage keys the test knows. To add a stage, add a `###` heading here and the
key to `_JOURNEY_STAGES` in the test.

The registry's `user_task` field (`pr_review | local_check | release | audit`,
from `vision.md`) is coarser and stays as it is. A stage is a step in the
journey; a user task is the reason for the run.

**Persona.** A maintainer of a native C/C++ library (often with Python
bindings) who ships binaries to consumers they do not control. Their question
at every stage: *"Will this change break someone, and can I trust the answer?"*

## Stages

Status counts come from the registry: complete / partial / modeled or planned
/ excluded by design. A use case may sit on more than one stage.

### `first_check`

**Goal:** see what changed between two builds, locally, in minutes.
**Touchpoints:** `abicheck compare old.so new.so`, `dump`, `--depth`, docs
quick start, the `explain-abi-change` agent skill.
**Use cases:** UC-WF-compare, UC-PLAT-linux-elf, UC-PLAT-windows-pe,
UC-PLAT-macos-macho, UC-ARCH-c-library, UC-ARCH-cpp-library, UC-ARCH-plugin,
UC-ARCH-python-api, UC-ARCH-kernel-btf, UC-ARCH-sycl (complete);
UC-WF-oneshot-deep (partial); UC-ARCH-header-only, UC-ARCH-cython-api
(planned); UC-ARCH-static-lib, UC-WF-mcp (excluded).
**State:** strong for shared libraries on Linux. Header-only libraries have no
first-class path yet (G4, G45).

### `adopt_ci`

**Goal:** make every PR fail when it breaks ABI, against the right baseline.
**Touchpoints:** GitHub Action, `actions/baseline`, `actions/check-target`,
`project plan`/`validate`, `--dry-run` cost preview, SARIF/JUnit/JSON output,
the `set-up-abi-compatibility-ci` agent skill.
**Use cases:** UC-WF-baseline, UC-WF-cli-contract, UC-REP-json, UC-REP-sarif,
UC-REP-junit, UC-TC-make-auto-query, UC-TC-build-emitted-facts,
UC-REPORTING-dry-run-cost-preview (complete); UC-TC-bazel-build-evidence
(modeled); UC-WF-abicc-compat (excluded).
**State:** works. Open: the Action still encodes some CLI semantics (ADR-070
D1, D2, D4) and the lifecycle model's P2 (ADR-047, G30).

### `pr_review`

**Goal:** a reviewer understands, from the PR comment, what broke and why.
**Touchpoints:** PR comment, SARIF annotations, Markdown/HTML report,
`--view root-cause|impact`, `--depth source`, the `check-abi-compatibility`
agent skill.
**Use cases:** UC-CHANGE-taxonomy, UC-REP-markdown-html, UC-TC-dual-abi,
UC-TC-flag-drift, UC-TC-modern-cxx-types, UC-TC-cxx-standard-floor and the
archetype/platform cases (complete); UC-CHANGE-inline-ns-version,
UC-TC-numpy-capi-envelope, UC-WORKFLOW-pr-source-tier (partial).
**State:** the most covered stage.

### `trust`

**Goal:** know how far the verdict can be believed — what evidence was used,
what was missing, what the scope was.
**Touchpoints:** evidence/assurance block, `not_collected`/`FAILED` facts,
comparability refusal, crosscheck, completeness axis, `known-gaps.md`.
**Use cases:** UC-PLAT-arch-guard, UC-WF-probe-matrix,
UC-TC-header-scope-robustness, UC-CHANGE-crosscheck-hygiene (complete);
UC-WF-oneshot-deep, UC-WORKFLOW-real-world-corpus, UC-WORKFLOW-pr-source-tier,
UC-CHANGE-crosscheck-example-corpus (partial).
**State:** **the weakest stage for its importance.** False-clean entries in
`known-gaps.md` (function-pointer signatures, castxml template defaults,
`debug=1` DWARF, export shrink without a declared surface) land here. Scope and
completeness (ADR-065) and fact adequacy (ADR-063) have no dedicated use case.

### `tune`

**Goal:** make the gate match the project's real promise — suppress noise,
accept intended breaks, declare the public surface.
**Touchpoints:** `--policy`, `--suppress`, `--contract`, `--acknowledgments`,
`--header`/`--exclude-header`, disposition audit in the report.
**Use cases:** UC-WF-stable-abi-subset, UC-TC-header-scope-robustness
(complete); UC-WORKFLOW-evidence-directed-scope,
UC-WORKFLOW-focusing-example-corpus (excluded).
**State:** **the features ship (ADR-010, 013, 044, 049, 067) but no use case
covers them.** Suppression, policy profiles, contract configuration and
acknowledgment need registry entries with evidence.

### `release`

**Goal:** decide the version bump and ship a release whose ABI promise holds.
**Touchpoints:** release/directory/package compare, semver recommendation,
bundle facts, wheel checks, Debian symbols.
**Use cases:** UC-CHANGE-semver-recommendation, UC-WF-compare,
UC-WF-debian-symbols, UC-WF-bundle, UC-WF-wheel-vendored,
UC-WF-stable-abi-subset, UC-WF-probe-matrix, UC-TC-glibc-floor (complete);
UC-CHANGE-inline-ns-version, UC-TC-wheel-deployment-claims (partial).
**State:** good. Open: bundle checks at `depth: headers` (ADR-023), history and
versioning policy (ADR-066) have no use case.

### `downstream`

**Goal:** know which consumers a change affects and whether a given
application or plugin still works.
**Touchpoints:** `--used-by`, `--used-by-manifest`, `--required-symbol`,
`--use-cases`, `deps tree`/`compare`.
**Use cases:** UC-ARCH-plugin, UC-WF-appcompat, UC-WF-stack-deps,
UC-TC-cxx-standard-floor (complete); UC-TC-numpy-capi-envelope (partial);
UC-ARCH-ffi-consumers (excluded).
**State:** works with supplied consumers. Open: prebuilt-consumer lifecycle in
the Action (vision workstream D) and per-finding `affected_use_cases`
(ADR-057).

### `audit`

**Goal:** assess a shipped artifact on its own, or over time.
**Touchpoints:** `compare --no-baseline`, security-hardening and glibc-floor
checks, dependency stack.
**Use cases:** UC-WF-stack-deps, UC-WF-security-hardening, UC-TC-glibc-floor,
UC-WORKFLOW-single-release-audit (complete);
UC-WORKFLOW-audit-example-corpus (partial); UC-WF-audit (planned);
UC-ARCH-static-lib, UC-REPORTING-scan-coverage-report (excluded).
**State:** partial. ADR-086 added static archives as a `--no-baseline` audit
operand, so UC-ARCH-static-lib's exclusion needs revisiting.

## Gaps the map shows

1. **`trust`**: false-clean results and missing scope/completeness use cases.
   For the current priority (L2, Linux ELF) this is the first stage to fix.
2. **`tune`**: shipped policy features with no use case backing them.
3. **History and versioning** (ADR-066) and **scope/completeness** (ADR-065)
   have no use case on any stage.
4. **Header-only** libraries cannot start the journey (`first_check`).
