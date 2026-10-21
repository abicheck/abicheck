# abicheck — oneDAL handover (PR #3693): analysis, fixes and testing

HEAD analysed: `026a6109e` (0.7.0). Analysis only; no repo files changed.
Detailed reports, with file:line references, are next to this file:
P1_P2.md, P3.md, P4.md, P5.md, P6_P7_P9.md, P8.md, P10.md.
Reproduction scripts are in `analysis/` and `../p4repro/`.

## 1. Status after re-verification

| # | Handover claim | Verdict at HEAD | Correction / new finding |
|---|---|---|---|
| P1 | Rules that match nothing are silent | **Confirmed, wider than reported** | The audit is built from records, not from configured rules. These are also missing: a reclassify rule that matches but changes nothing, and a suppression rule shadowed by an earlier one. Stale suppression rules do appear in `suppression_audit.stale_rules`, which disagrees with `disposition_audit`. The `ci-gating.md` claim that `suppression.strict` fails on unused rules is false. |
| P2 | `id:` gives exit 64 | **Confirmed** | `rule_id` is `label`, else `reason`, else the target verdict. Two unlabelled rules with the same `to:` collapse into one row. |
| P3 | No per-job `require_complete` | **Confirmed, worse** | `check-target`'s `analysis-assurance-complete` is one-way: `false` cannot lower a config `true`. No surface can turn the gate off for one job. Trap: `check-project.yml:1009` forwards `false` for undeclared cells. |
| P4 | `setArrays<double>` false positive | **Confirmed, root cause wider** | The removal path never consults the new binary's export table; it is computed at `diff_symbols.py:534` and never used. A binary-identical symbol can be reported `func_removed`. castxml lists an instantiation only when a header translation unit uses it. |
| P5 | Internal template reported as API break | **Confirmed, bug class of 53 kinds** | There is no existing public-surface demotion for export-removal kinds, so "apply the same rule" has nothing to apply. A central evidence gate is needed. |
| P6 | Stray asset beats baseline-set | **Does not reproduce** | Fixed by #1631, including in the root Action. **New:** a file-path `abi-baseline` silently ignores `baseline-profile` and `baseline-target`. Selection is duplicated in bash in two places. |
| P7 | Integrity only in shell | **Confirmed** | Nothing computes or checks an archive-level digest. A snapshot tampered with and repacked with a matching manifest passes. |
| P8 | No acknowledgments surface | **Partly stale; real gap is bigger** | Acknowledgments load via `.abicheck.yml acknowledgment.file`, but they never change the gate for a break: an acknowledged removal still exits 4. Expired or unused acknowledgments are silent. **Possible trust leak, from reading code only:** the overlay does not strip `acknowledgment.file` (`action_config_overlay.py:220`). |
| P9 | `actions/baseline` has no `build-config` | **Stale** | The input exists and is tested; it was already present at the old pin. |
| P10 | Skill drift | **Confirmed, worse** | The skill pins `@v0.7.0`, which its own range excludes. All three skills and `docs/use/agent-skills.md` carry the stale range. `--clobber` also appears in user docs. `CHANGELOG.md` says 0.7.0 is unreleased, but the tag exists. |

P4 and P5 overlap. `inline_function_removed` with no headers is one of P5's 53 kinds, and the P4 session reproduced `inline_function_removed` only when the **old** side was not exported. Check oneDAL's old `surface_facts.binary_exported` against readelf.

## 2. Proposed fixes, in implementation order

1. **P1+P2+P8, one rule inventory.**
   - **Inventory:** each configured disposition rule (reclassify, suppression, acknowledgment) gets a stable `id`. The `id` is optional, file-unique, and otherwise derived from the rule's canonical identity.
   - **Audit row:** each rule gets exactly one row, with `matched`, `disposed`, `shadowed` and `status` (`active`, `unused`, `not_evaluated`, `expired`).
   - **Opt-in gate:** `policy.unused_rules: allow|warn|block`. It contributes 0 or 1 to the exit code, folded with `max`.
   - **Acknowledgment surfaces:** `--acknowledgments` on the CLI and the Action, through one resolver.
   - **Accepting breaks:** a policy for whether an acknowledgment may accept a break. Maintainer decision.
   - **Trust:** strip `acknowledgment.file` from untrusted overlays.
   - **ADR:** amend ADR-067.
2. **P5, central evidence gate in `policy/`.**
   - **The gate:** an API_BREAK kind that the binary alone can produce keeps API_BREAK only with observed header or source contract evidence. Otherwise it is demoted to COMPATIBLE_WITH_RISK, with a disposition record.
   - **Detectors:** remove the hard-coded `public_reachable=True` in `std_reexport_removed` and `tag_type_renamed`.
   - **Cost:** this changes exit codes for runs without headers, so it needs an ADR and a changelog fragment.
3. **P4, in two steps.**
   - **F1:** check the new export table before choosing a removal kind (around `diff_symbols.py:439`). This was validated in-process and changes only the binary-identical case.
   - **F2:** when the primary template is still declared in the new headers, report an export or visibility kind, not a declaration removal.
   - **What not to do:** don't demote WEAK export loss to RISK by default. Cases 217 and 218 encode BREAKING, and earlier linkage-blind attempts were reverted (known-gaps). Leave it to a consumer `binding: weak` policy.
4. **P3, tri-state `analysis-assurance-complete` input.**
   - **The input:** on both the root Action and `check-target`. Values are empty, `true` and `false`, with no default. It reuses the `dso-only` overlay, including on the scalar path.
   - **check-project:** must forward an empty value for cells that don't declare the input.
5. **P7, archive digest.**
   - **Output:** `stage-baseline` emits `archive-sha256`.
   - **Check:** the root Action and `resolve-baseline` accept `expected-archive-digest` and fail closed on a mismatch.
   - **Limit:** stronger tamper defence needs a pinned digest, immutable releases, or attestation.
6. **P6 residual, one baseline resolver.** Move archive selection into one shared resolver, and reject a file-path `abi-baseline` combined with a profile or target. Write the baseline source into the JSON report.
7. **P10, skills and docs.**
   - **Version range:** bump to `<0.8.0` in three skills and in `agent-skills.md` once 0.7.0 is confirmed released.
   - **Overwriting baselines:** replace `--clobber` with an existence check that fails.
   - **Baseline-sets:** add a baseline-set and `resolve-baseline` path.
   - **Docs:** fix the `strict` doc claim from P1.

## 3. Why the tests missed these

There are four recurring patterns.

1. **Tests assert the positive path only.**
   - **P1:** rules were tested only when they matched. One test asserts the bug: `test_no_reclassify_rule_leaves_the_overlay_absent`. The conservation tests generate zero-match rules but assert totals only.
   - **P8:** expired and unused acknowledgments were never asserted.
2. **Unit tests run below the verdict.**
   - **Detectors in isolation:** detector tests run the detector alone (P5), and the acknowledgment engine is unit-tested but not tested end to end (P8).
   - **Evidence always present:** the verdict property tests always use `from_headers=True` (P5).
   - **Identical environments:** fixtures build both sides with the same flags and the same header (P4, `test_export_loss_binding_invariant.py`).
3. **Surface checks test existence, not behaviour.**
   - **ADR surfaces:** `check_adr_surfaces` passes while ADR-071 cites a retired input (P3).
   - **Input parity:** the parity check confirms inputs are referenced, but not their names, defaults or direction (P3, P6).
   - **Skills:** the skill test checks only the minimum of the version range, and the grader accepts `--clobber` (P10).
4. **Logic lives in two places.** Baseline selection is in two bash copies (P6), so a fix can land in one and miss the other.

## 4. Generalized testing: test the invariant, not the reported input

Each bug is an instance of a property that should hold for **every** element of a set: every rule, every kind, every action input, every skill. A generalized test does three things:

- It **enumerates the set from its source of truth**: the kind registry, the parsed `action.yml` files, the configured rules, `skills-src/`. A new member is then covered automatically.
- It checks against an **oracle independent of the implementation**: readelf, header text, a hand-written first-match function, `pyproject` or `CHANGELOG` versions.
- It includes a **positive control and a negative control**, so the test can be shown to have teeth.

Where a classification is required, add a **completeness ratchet**: a test that fails when a new member is unclassified. `test_canonical_finding_id_completeness.py` is the pattern.

| Invariant | Enumerates | Oracle | Catches |
|---|---|---|---|
| I-RULE: one audit row per configured rule, with `matched` equal to the first-match oracle; an unmatched rule is metamorphically inert | Generated rule sets with Hypothesis: zero-match, overlapping, duplicate, expired, all three rule types | Independent first-match function | P1, P2, P8 |
| I-EVID: with no header or source evidence, no API_BREAK verdict; with headers, API_BREAK is preserved | Every `API_BREAK` kind, sorted into three groups with a completeness test | Evidence level of the input | P5, part of P4 |
| I-DECL: no declaration-removal kind for a symbol still declared in the new headers; export kinds only when readelf shows an export change | 48-cell matrix: {free template, member template, class-template member, inline member} × {WEAK→LOCAL, WEAK→absent, GLOBAL→hidden} × header-instantiates or not × extern template | readelf plus header text | P4 (new BugClass `detector.removal_from_header_enumeration`, catalog case219) |
| I-PARITY: every config key overridable from one Action is reachable from all of them, or listed as an exception; overlay inputs have no default; names match; the override works both directions | All `action.yml` files, parsed | Config schema | P3, P6 project-ref default drift |
| I-REACH: every policy capability is reachable from the CLI, the Python API and the Action, and visible in the report | Capability table | One shared fixture run through each surface | P8 |
| I-BASE: one resolver decides baseline selection over every combination of {no assets, one stray, two stray, baseline-set, file path} × {profile and target given or not}; a consistent repack fails when a digest is pinned | Decision table | Expected-source table | P6, P7 |
| I-SKILL: each skill's pinned tags satisfy its range; the range contains the newest released version; every Action key a skill uses exists; every baseline workflow is covered by a skill; no `--clobber` without an allowlist | `skills-src/` | `action.yml` files and CHANGELOG | P10 |

The prototype tests already show these work on HEAD:
- **I-PARITY** flags P3 and `check-target`'s default `'false'`.
- **I-SKILL** flags the pin outside the range and `--clobber`.
- **F1** changes exactly 1 of the 24 matrix rows.

## 5. Decisions needed from maintainers

1. Is 0.7.0 released? The tag and CHANGELOG disagree.
2. P5 changes exit codes for runs without headers. Is that acceptable, and is an ADR needed?
3. P8: may an acknowledgment accept a break, or only annotate it? Where may acknowledgment files come from on `pull_request` runs?
4. P1: does `matched_count` keep meaning "disposed"? Is the unused-rule gate per comparison or per release? Is the `rule_id` value change acceptable?
5. P4: check the old snapshot's `binary_exported` for `setArrays<double>` with readelf, to explain exit 2 against "old WEAK".
