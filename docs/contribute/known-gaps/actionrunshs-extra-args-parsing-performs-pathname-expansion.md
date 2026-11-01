---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `action/run.sh`'s `extra-args` parsing performs pathname expansion (globbing), not just word-splitting, and no site disables it — investigated, deliberately not fixed (CodeRabbit review, PR #998, ADR-064's effective-format-override fix)

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-31): CLOSED.** Fixed by hardening all four sites together, as this entry asked: `run.sh`'s `CMD` append, `_extra_args_options` (behind `_effective_format`, `_extra_args_has_write_flag` and `_extra_args_write_json_path`) and `_extra_args_names_label` all tokenize through one noglob splitter, `_extra_args_split`, so detection and invocation still agree token for token. `actions/check-target`'s overlay tokenizer was a fifth site and is fixed too. Pinned by `tests/test_action_extra_args_noglob.py`, which includes a `repo_scan` guard against any new unquoted user-input expansion. The history below is kept as written.

`_effective_format()` (added by that PR),
`_extra_args_has_write_flag()`, `_extra_args_write_json_path()`, and the
real command assembly (`CMD+=($INPUT_EXTRA_ARGS)`) all read
`$INPUT_EXTRA_ARGS` via an unquoted `set --`/array-append expansion, which
bash expands for both word-splitting AND filesystem globs. A crafted
`extra-args: '*'` (or any value containing a bare `*`/`?`/`[...]`) run in a
workspace that happens to contain a file whose name looks like a CLI flag
(e.g. `--format=json`) would have that filename silently substituted in as
a real argument -- an unintended, workspace-content-dependent flag
injection. `add_flag`'s sibling `_split_legacy_value` already hardens
against exactly this class (`set -f`, Codex/report finding P2.2), so the
precedent for fixing it exists.
**Not fixed here**, for a reason specific to this PR: `_effective_format()`
exists only to predict, from `$INPUT_EXTRA_ARGS`, what `--format` value the
real `CMD+=($INPUT_EXTRA_ARGS)` expansion will actually produce -- so it
reads that variable the *same* (unsafe) way on purpose. Disabling globbing
in `_effective_format()` alone while leaving `CMD` assembly unprotected
would not close the vulnerability (the real invocation would still glob)
and would *introduce* a new divergence between what this detection
function predicts and what Click actually receives -- worse than today's
status quo of "both glob identically, so they can't disagree." Closing
this properly means hardening all four sites together in one coordinated
change (`CMD` assembly, `_effective_format`, `_extra_args_has_write_flag`,
`_extra_args_write_json_path`), verified against a hostile-glob test
corpus the way `test_action_run_sh_helpers.py`'s
`TestAddFlagHostileScalarCorpus` already exists for `add_flag`/
`add_sided_flag` -- a scoped, standalone follow-up, not a drive-by change
bundled into a PR whose actual objective was the effective-format fix
itself.
