---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Per-side `--debug-info` transports are not in the config tier

Index: [Known gaps, closed](../archive/known-gaps-closed.md)

**Status (2026-10-31): CLOSED** ([archive](../archive/known-gaps-closed.md)). `debug.info` is now the config home of `--debug-info` (ADR-091 D4, 2026-10-31 amendment). It is a list of entries in the flag's own `[old=|new=|both=]PATH` grammar, so the per-side home is the D3 side qualifier, not a pair of `debug_info.old`/`debug_info.new` keys. That keeps one grammar on the CLI and in config. A side's own entries replace the shared ones for that side, as on the command line. Relative paths resolve against the config file. The flag stays, wins, and is never merged with the list. `dump` has one operand, so it refuses an `old=`/`new=` entry by name. It backs a core flag, so `--set` and the typed `config_overrides` refuse it, naming `--debug-info`, like every other core key. Resolver: `abicheck/workflows/config_run_inputs.py`. Tests: `tests/test_config_run_input_homes.py`.
