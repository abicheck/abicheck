# Plugin Systems (host ↔ plugin)

Plugin architectures have a **two-sided ABI contract** that a plain `compare`
doesn't fully capture on its own:

- A **host** `dlopen`s each plugin and resolves a fixed set of **entry-point
  symbols** (`dlsym("plugin_init")`, …). If a plugin upgrade drops or changes
  one of those, the host fails to load it — *regardless* of the plugin's
  library-wide verdict.
- The host and plugin are usually built **in-process**, so some changes that
  `strict_abi` flags (e.g. calling-convention nuances) are not relevant.

`abicheck` addresses both sides:

| Concern | Tool |
|---------|------|
| Does plugin **v2** still satisfy the host's required entrypoints? | `abicheck compare --required-symbol` |
| Downgrade in-process-only ABI noise to the right severity | `--policy plugin_abi` |

> **History note:** this used to be a standalone `abicheck plugin-check`
> command. The pre-1.0 CLI reset folded it into
> `compare --required-symbol`/`--required-symbols` (ADR-043) — the full
> library comparison runs once, and the entrypoint-scoped result becomes the
> primary verdict/exit code, with the full-library verdict kept as
> informational context. Mutually exclusive with `--used-by`.

---

## `compare --required-symbol` — the host's load contract

Give the old and new plugin (binary **or** JSON snapshot) plus the host's
required entrypoints, and `--required-symbol`/`--required-symbols` reports
whether the new plugin still satisfies the host — the plugin-load mirror of
`--used-by`.

```bash
# Entrypoints listed inline (repeatable flag):
abicheck compare plugin.v1.so plugin.v2.so \
  --required-symbol plugin_init --required-symbol plugin_run

# …or from a file (one symbol per line, '#' comments allowed):
abicheck compare plugin.v1.so plugin.v2.so --required-symbols host.syms
```

A `host.syms` file is just the symbols the host resolves:

```text
plugin_init
plugin_run     # core entrypoint
plugin_shutdown
```

`--required-symbol` and `--required-symbols` combine: values from both are
merged into one required-entrypoint set.

### What it reports

- **Missing entrypoints** — required symbols the new plugin no longer exports
  (a hard load break).
- **Incompatible changes affecting the host** — diff changes that touch a
  required entrypoint (e.g. a signature change), scoped exactly like
  `--used-by` scopes changes to an application's used symbols.
- A host-scoped **verdict** and entrypoint **coverage** percentage, folded
  into the run's primary verdict/exit code alongside the full-library
  verdict.

A library-wide `BREAKING` drop of a symbol the host never resolves leaves the
host-scoped result **COMPATIBLE** — that consumer-scoped distinction is the
whole point.

### Exit codes

`compare --required-symbol`/`--required-symbols` uses the same exit codes as
plain `compare` (see [Exit Codes](../reference/exit-codes.md)), computed from
the worst of the full-library verdict and the entrypoint-scoped verdict:

| Code | Meaning |
|------|---------|
| `0` | `COMPATIBLE` — the new plugin still satisfies the host |
| `2` | `API_BREAK` — source-level break affecting a required entrypoint |
| `4` | `BREAKING` — a required entrypoint was dropped or is ABI-incompatible |
| `64` | usage error — bad arguments/invocation |

---

## `plugin_abi` policy

For in-process host/plugin builds, use the `plugin_abi` policy so
calling-convention–style findings that do not matter for a co-built
host/plugin pair are weighted appropriately:

```bash
abicheck compare plugin.v1.so plugin.v2.so --policy plugin_abi
abicheck compare plugin.v1.so plugin.v2.so \
  --required-symbol plugin_init --policy plugin_abi
```

See [Policy Profiles](policies.md) for the full policy model.

---

## Python API

```python
from abicheck.appcompat import check_plugin_host_contract
from abicheck.service import resolve_input

old = resolve_input("plugin.v1.so")
new = resolve_input("plugin.v2.so")
result = check_plugin_host_contract(old, new, {"plugin_init", "plugin_run"})

print(result.verdict, result.missing_entrypoints, result.coverage)
```
