<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the leading HTML comment
symbols) and describe the change, in past tense. Add as many bullet points
as needed. If change doesn't need a section, remove it entirely.
-->
### Security

- **Disabled the `--verify-runtime` execution probe.** It previously
  executed a real consumer binary with the analyzed OLD/NEW shared
  library staged via `LD_LIBRARY_PATH`, letting an analyzed artifact's
  ELF constructors and other load-time code run with this process's
  privileges, environment, and credentials. `run_runtime_probe` is now a
  non-executing no-op — it always returns `attempted=False` and never
  spawns a process — and `--verify-runtime` is documented as a deprecated,
  inert compatibility shim. Use the static `--used-by` scanner for
  undefined-symbol corroboration instead; it never executes anything.
- **Hardened baseline-archive extraction against workspace-shadowing
  imports.** `actions/resolve-baseline/run.sh`'s `python3 -c` invocations
  (used to extract `.tar`/`.tar.gz`/`.tgz`/`.tar.zst` baseline archives)
  now run under `python3 -I` (isolated mode), so a malicious caller
  repository can no longer plant its own `abicheck/package.py` (or point
  `PYTHONPATH` at one) and have it imported in place of the installed
  distribution.
- **Isolated `scripts/verify.py`'s Python-tool invocations from
  repository-root shadowing.** `ruff`/`mypy`/`pytest`/`build`/`twine`
  module lookups (in `scripts/verify.py`, `scripts/check_ai_readiness.py`,
  `scripts/gen_repo_facts.py`, `scripts/build_and_check_distribution.py`)
  now route through a new `scripts/run_isolated_module.py` runner
  (`python -I -m ...`-equivalent) instead of a bare `python -m`, so a
  PR-planted repository-root `pytest.py`/`mypy.py`/`ruff.py`/etc. can no
  longer be imported in place of the real, installed tool while these
  checks run with `cwd=ROOT` — closing a way CI's own verification gates
  could have been made to falsely report success.
