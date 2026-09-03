### Fixed

- **`tests/test_action_run_sh_scan_not_comparable.py` failed on `windows-latest`
  after this PR's `action/run.sh` edits grew the extracted `scan` exit-code
  `case ... esac` fragment.** Passed as a single subprocess argv string, Windows
  reconstructs it via `list2cmdline` and Git Bash's own MSYS runtime then
  re-parses the result with its own, not-quite-identical rules — the two
  disagree on a large, quote-heavy argument and can corrupt it (observed as
  ``syntax error: unexpected end of file from `case' command``, on a script
  that is valid bash and passes identically on every other platform).
  Migrated to the same file-based dispatch (`_run_bash_script`, writing the
  script to a temp file rather than passing it via `-c`) already used by this
  file's own siblings (`test_action_run_sh_helpers.py`,
  `test_action_run_sh_scan_evidence_contract_error.py`).
