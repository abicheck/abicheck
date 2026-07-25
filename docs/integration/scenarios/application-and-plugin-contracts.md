# Scenarios S22 & S23: Application and Plugin Contracts

Not every check is "does this library's own public header/binary surface
stay compatible." Two related but distinct questions
[ADR-047](../../development/adr/047-github-actions-integration-model.md)
§8 names S22 and S23:

- **S22 — application compatibility.** Will *this specific application*
  still work with the new library version? Scopes the verdict to only what
  the application actually calls — a break in a symbol the app never uses is
  not a break for this check.
- **S23 — plugin/`dlopen`/`dlsym` contract.** Does a fixed set of *required*
  symbols still exist, at all — not a public-header ABI comparison, since a
  plugin/host contract is usually a narrow, explicitly-named symbol list, not
  "everything public."

Both are a `target-kind` on an otherwise ordinary `check-target`/
`check-project.yml` check, not a different command or workflow.

## S22: `target-kind: app-consumer`

```yaml
targets:
  myapp-consumer:
    kind: app-consumer
    consumer_binary_pattern: "bin/myapp"
    library: libpvxs   # the check's baseline/candidate lookup redirects through this
    checks:
      - channel: accepted-main
        depth: headers
```

The check's own reporting identity stays `myapp-consumer`, but its
baseline/candidate library lookup redirects through `library:`'s target — the
["library redirect"](../concepts.md#target) every app-consumer/plugin-contract
target uses. `check-target`'s `consumer-binary` input forwards to the root
Action's `--used-by`; see
[Application Compatibility](../../user-guide/appcompat.md) for the full
`--used-by` scoping model, and the
[`check-target` reference](../../reference/check-target.md) for the target-kind
input table.

## S23: `target-kind: plugin-contract`

```yaml
targets:
  ioc-plugin-contract:
    kind: plugin-contract
    contract_file: "contracts/ioc-plugin.syms"   # one required symbol per line, # comments allowed -- NOT YAML
    library: libpvxsIoc
    checks:
      - channel: accepted-main
        depth: binary
```

`contract-file` forwards to the root Action's `--required-symbols`. See
[Plugin Systems](../../user-guide/plugin-systems.md) for the full
`--required-symbol`/`--required-symbols`/`--policy plugin_abi` model.

## Neither works with `channel: none`

`scan` mode has no `--used-by`/`--required-symbols` equivalent — an
app-consumer/plugin-contract check has no scope to audit without a baseline
to compare against. Give it a real channel, or use `kind: library` for a
no-baseline audit ([S5](single-build-audit.md)) instead.

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [Application Compatibility](../../user-guide/appcompat.md) — the full `--used-by` reference.
- [Plugin Systems](../../user-guide/plugin-systems.md) — the full plugin/dlopen contract reference.
- [`check-target` Action Reference](../../reference/check-target.md) — the `target-kind` input table.
- [Project Targets Schema](../../reference/project-targets-schema.md) — the `.abicheck.yml` `kind: app-consumer`/`plugin-contract` schema.
