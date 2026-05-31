# G5 — Plugin host↔plugin contract (the dlopen direction)

**Registry:** `UC-ARCH-plugin` (`partial`)
**Effort:** M · **Risk:** low

## Problem

`appcompat` answers the *consumer→library* direction (will an app break when a
library it links changes), and the `plugin_abi` policy downgrades
calling-convention kinds for in-process host/plugin builds. But the actual
`dlopen` failure mode is a **two-sided contract**:

- the **host** resolves a fixed set of entry-point symbols from each plugin it
  loads (`dlsym`), and
- the **plugin** resolves symbols the host exports back to it.

`tests/test_workflow_scenarios.py` (this PR) demonstrates the host-side contract
synthetically, but there is no first-class way to check *"does plugin v2 still
satisfy host H's required entrypoints?"* nor a runnable example.

## Goal & acceptance criteria

- [ ] An explicit host-contract check: given a plugin (old/new) and a declared
      set of required entrypoints (a small manifest, or symbols extracted from a
      host binary), report whether the plugin still satisfies the host —
      reusing `appcompat`'s symbol/version resolution machinery in the
      plugin-load direction.
- [ ] A runnable `examples/` fixture: a host that `dlopen`s a plugin, a v1/v2
      plugin pair, and an `app`-style demo showing the load failure when a
      required entrypoint is dropped — with an asserted verdict in
      `ground_truth.json`.
- [ ] Docs: a "plugin systems" section wiring `plugin_abi` + the host-contract
      check together.

## Design

1. **Reuse, don't rebuild:** `appcompat.parse_app_requirements()` already
   extracts required (undefined) symbols + version needs from an ELF/PE/Mach-O
   consumer. A host binary that `dlopen`s plugins won't list plugin symbols as
   undefined (they're resolved at runtime), so add a manifest input
   (`--host-contract entrypoints.txt`, or `--host <binary>` for symbols the host
   *exports* to plugins) and run the same availability check against the
   plugin's exports across v1→v2.
2. **CLI:** a thin mode on `appcompat` (or a small `plugin-check` command in
   `cli_stack.py` style) — minimal surface, delegating to `appcompat.py`.
3. **Verdict:** missing required entrypoint → `BREAKING`; reuse
   `_compute_appcompat_verdict`.

## Files & surfaces

- `abicheck/appcompat.py` (host-contract input + check), `abicheck/cli_appcompat.py`
  (flag), or a new `abicheck/cli_plugin.py` registered per the
  "Adding a new top-level command" recipe in `/CLAUDE.md`.
- `examples/caseNN_plugin_host_contract/` (host + v1/v2 plugin + README + ground truth).

## Tests

- Unit: host-contract check over synthetic plugin snapshots (extend
  `test_workflow_scenarios.py`).
- `@pytest.mark.integration`: build host + plugin pair, assert load break.

## Out of scope

Reverse-engineering implicit `dlsym(name)` string usage from a stripped host
(out of static scope). Versioned plugin ABIs beyond ELF symbol versioning.
