<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`action/run.sh` and `actions/stage-baseline/run.sh`'s `{profile}`
  substitution** no longer uses `${asset_template//\{profile\}/$PROFILE}`
  — Bash 5.2's default `patsub_replacement` shell option gives `&` in
  that construct's replacement *text* the special "insert the matched
  pattern text" meaning (like sed's `&` backreference), so a
  `baseline-profile` containing a literal `&` (e.g. `linux&asan`)
  previously expanded to `...{profile}...` instead of the literal string
  on Bash 5.2+, while resolving correctly (no such interpretation) on
  Bash 3.2 — the same template/profile pair could resolve to two
  different asset names purely depending on which runner published vs.
  consumed it. Replaced with a portable `_substitute_literal` helper
  using prefix/suffix pattern-*removal* (`%%`/`#`) plus plain string
  concatenation, neither of which carries any special-character semantics
  in either position, in both files.
- **`tests/test_protect_committed_baseline_workflow.py`** now invokes the
  extracted check script via a temp file instead of `bash -c "<script>"`
  — the script has grown large enough that Windows' Git-Bash `-c`
  argument passing truncates it mid-parse, surfacing as a bare syntax
  error unrelated to the script's actual content (confirmed by a real
  windows-latest CI failure). Mirrors the identical `_run_bash_script`
  helper `test_action_run_sh_baseline_set_fallback.py` and
  `test_action_run_sh_dry_run_baseline.py` already use for the same
  reason.
