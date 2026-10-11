---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The composite Action's single `--write` slot can leave its unconditional coverage/assurance/severity floors without a structured report

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** The SARIF/text fallback part is superseded. The remaining non-JSON --write blindness is an explicitly accepted limitation per action/AGENTS.md 'Known, accepted limitation'.


**Superseded** (ADR-063 Phase 6, Track T8): the SARIF-fallback/HTML-gap
account this entry used to carry is retired along with the rendered-prose
readers it described. `_report_compat_verdict`/`_severity_gate_exit`/
`_coverage_gated`/`_assurance_gated` no longer have a "rendered markdown/
text" fallback tier at all (`_text_report_content` is deleted), nor a
SARIF `runs[0].properties.abiVerdict` branch — every one of them is
JSON-only now (`run_outcome`, then the legacy per-field JSON, then
nothing), per `action/AGENTS.md`'s "How `run.sh` resolves the verdict it
publishes". A `format: html` primary was never coherent evidence for this
group of readers, so its own absence is no longer a special case either.

What remains, narrower than before: the CLI's `--write` option is still
single-valued (`secondary_output.py`'s `--write FORMAT=PATH`, not
`multiple=True`), and `scan`/`compare`'s own internal `--write
json=$PR_JSON` sidecar injection (unconditional since Track T8 — no longer
gated on `pr-comment`, closing the gap this same track's own Codex review
found in the `pr-comment: false` case) is still skipped whenever the
step's own `extra-args` already supplies a `--write` of any format
(`_extra_args_has_write_flag`), since `extra-args` is appended after the
injection and Click keeps only the last occurrence of a repeated
non-multiple option — a second, JSON-targeted `--write` this script
appended would always lose to the user's own later one and never execute.
When that user-supplied `--write` targets `json=`, `_extra_args_write_json_
path` already recovers it, so `_json_report_src` still finds structured
evidence. When it targets a **non-JSON** format (`-o text=...`,
`-o markdown=...`, ...), there is no JSON anywhere, and ADR-049's
unconditional contract-coverage/analysis-assurance floors plus the
severity-category gate genuinely go blind for that one run — accepted, not
fixed, per `action/AGENTS.md`'s own "Known, accepted limitation"
paragraph, since closing it would mean either silently discarding the
user's explicit `--write` choice or unconditionally re-running the
analysis a second time purely to obtain JSON (a materially larger,
separate trade-off `_maybe_post_pr_comment` only accepts today for the
sticky-comment feature, under its own narrower guards). If this becomes a
real reported problem rather than a review-found edge case, the honest fix
is the same one this entry always named: make `--write` accept multiple
`FORMAT=PATH` operands (a real CLI capability change touching
`secondary_output.py` and every command that declares the option, not
just this Action script) — not another per-reader special case.
