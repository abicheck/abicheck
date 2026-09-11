### Security

- **The composite Action's "Generate assurance-overlay config" step no
  longer writes its output to a predictable, in-checkout path.** A
  malicious PR could commit a symlink at
  `check-target-assurance-config.yml` pointing anywhere the runner user
  can write, and the step's `open(path, "w")` would follow it and
  overwrite the target with attacker-influenced YAML. The output now goes
  to a private, freshly `mktemp`-created file under `$RUNNER_TEMP` — a
  runtime-random name nothing in the untrusted checkout could have
  pre-planted a symlink at — forwarded to the internal analysis step as
  the overlay step's own `config-path` output. Independent of the earlier
  `sitecustomize.py` startup-isolation fix, which addresses a different
  vulnerability (code execution during interpreter startup, not where the
  write lands).
