---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A stored-`BundleFacts` baseline's narrower format set is not pre-checked by the Action

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** action/validate-inputs.sh:327-338 still checks only the release allowlist (json|markdown|junit|oneline|html) and has no stored-BundleFacts narrowing. No --probe-formats capability exists.


`actions/`' preflight (`action/validate-inputs.sh`) mirrors every one of
`compare`'s format allowlists so a bad `--format` fails before the
multi-minute toolchain install rather than after it — except one. A stored
`BundleFacts` OLD side with a directory/package NEW side renders `json` or
`markdown` only (`workflows/bundle_facts_stored_request.py`),
narrower than the release allowlist's `json|markdown|junit|oneline` that
preflight applies when *either* operand is release-style. So
`format: junit` on that shape passes preflight and then fails in the CLI with
a usage error (exit 64, surfaced as `VERDICT=ERROR`). A late clear failure,
not a wrong result — but late.

It is not fixed here because both available fixes are worse than the gap:

- **Classify in shell.** Whether a file *is* a stored `BundleFacts` document
  is answered by `storage/bundle_facts_codec.looks_like_bundle_facts_document`,
  a deliberately two-tier classifier (explicit `artifact_type` marker, then a
  v1-only shape fallback with documented, accepted false positives). A shell
  re-implementation is exactly the duplication `_is_release_style_operand`'s
  own comment warns about, and that one is cross-checked against the live CLI
  by a test — this one could not be, since the classifier operates on decoded
  JSON, not a path.
- **Read the document at preflight.** The operand is attacker-controlled in
  the PR-checkout case, which is the whole reason the decode-node ceiling is
  config-only and the remedy this PR rewrote says only an explicit `--config`
  may raise it. Decoding it in a preflight step that runs *before* those
  limits exist reopens precisely that surface.

A path-extension heuristic (`*.json`/`*.json.gz`/`*.json.zst`) was considered
and rejected: it would wrongly reject `junit` for a stored `ProjectSnapshot`
OLD side, which is the same shape on disk. The honest fix is to make the
narrowing a *CLI-reported capability* the Action can query cheaply (a
`compare --probe-formats` style answer, or having the CLI validate formats
before resolving operands), which is a real design slice rather than a guard.
Until then the Action's `format` input documents the narrowing and says
plainly that it is not pre-checked. Found by Codex review on PR #1237.
