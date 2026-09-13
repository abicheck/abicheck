### Security

- **`action/run.sh` no longer lets a workflow input forge a GitHub workflow
  command.** Six of its `::error::` messages interpolated an `INPUT_*` value
  straight into a line-delimited annotation, so a value carrying a newline
  (or a percent-encoded one, which the runner decodes) ended the annotation
  and everything after it was parsed as a *new* workflow command — e.g.
  `require-complete-analysis: "true\n::warning::forged"`. The messages now
  route through one `_error_annotation` helper that collapses CR/LF, escapes
  `%`, and emits with `printf` rather than `echo` (a literal `\n` is inert
  under a plain `echo` but becomes a real line under `xpg_echo`). This
  matches the defense `action/validate-inputs.sh` already had, and the
  attacks are executed by `tests/test_action_run_sh_injection.py` rather
  than asserted from the script's text.
