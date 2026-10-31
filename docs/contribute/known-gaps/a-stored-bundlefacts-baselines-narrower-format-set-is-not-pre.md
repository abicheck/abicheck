---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: historical
generated: false
---

# A stored-`BundleFacts` baseline's narrower format set is not pre-checked by the Action

Index: [Known gaps](../known-gaps.md) (closed; see the [archive](../archive/known-gaps-closed.md))

**Status (2026-10-31): CLOSED.** `action/validate-inputs.sh` now refuses, before the dependency install, a `format` outside the generated `stored-bundle-facts` set (`STORED_RELEASE_FORMATS`) when the OLD side (`old-library`, or a local-file `abi-baseline`) is a stored BundleFacts document. The classification is not re-implemented: `action/stored_bundle_facts_probe.py` imports the CLI's own `workflows/bundle_compare_operand.looks_like_stored_bundle_facts` from the Action's source tree (the tree `install-abicheck.sh` installs and `cli-surface.txt` is generated from). Its import closure is the standard library, so it runs before the install, under `python -I` so the untrusted checkout is never on `sys.path`; it reads a bounded prefix only, never fully decoding the operand. When the runner's pre-installed Python is older than 3.11, the tree is not importable, or the import resolves outside the Action's tree, the probe answers "unknown" and the CLI still enforces the narrowing after install. Tests: `tests/test_action_stored_bundle_facts_preflight.py` (for eight operand shapes x seven formats x both OLD inputs, the preflight refuses exactly when `abicheck compare` exits 64 with the stored-format error; a planted `sitecustomize.py`/`abicheck/` in the working directory is never imported; the probe runs with no site-packages).


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
