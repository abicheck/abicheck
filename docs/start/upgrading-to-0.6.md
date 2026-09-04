---
doc_type: migration
audience:
  - library-maintainer
  - ci-owner
level: intermediate
lifecycle: active
generated: false
---

# Upgrading to 0.6

> **This page describes unreleased changes on `main`.** The version installed
> from PyPI/conda-forge (`pip show abicheck`, or `pyproject.toml`'s `version`
> on the tag you're building from) is the latest *published* release — this
> migration doesn't apply until that version reaches `0.6.0`. If you're not
> on a `main` checkout, check `CHANGELOG.md` for whether `0.6.0` has actually
> shipped before following this page.

Several behavioral and API changes accumulated across recent releases,
tracked individually in `changelog.d/` fragments (see `CHANGELOG.md`).
This page collects them into one migration story instead of asking you to
piece it together from dozens of fragments — organized around the version
already used as the "Changed in 0.6" marker in [Python
API](../use/python-api.md).

## What might actually break your scripts

| Change | What breaks | Fix |
|---|---|---|
| `CompareResult` instead of a bare tuple | `result, old, new = run_compare(...)` (positional unpack) | `result, old, new = run_compare(...).as_tuple()` — one-line fix |
| Authoritative contract evaluation | If you already pass `--contract`, the *compatibility verdict* can only move one direction — same or **less severe** — since policy now scores just the `EVALUATED` subset it used to score in full (a finding proven out-of-contract stops blocking; one that can't be resolved stops gating as an ABI break — it still stays in `changes`/audit ledgers, `NOT_EVALUATED`). The **overall process exit** can move the other way too, but via the separate mechanism in the next row: the newly-authoritative contract-coverage contribution can raise a clean exit to `1` | Re-check any script that parsed `verdict`/exit code under `--contract` — see [Contract-Aware Compatibility](../learn/contract-aware-compatibility.md) |
| Contract-coverage exit `1` | A script that treats every exit `1` as "a severity error" will now also see `1` for incomplete contract evidence | Read `contract_coverage_exit_contribution` (or the severity block's own pre-fold `exit_code`) to tell the two apart — see [Exit Codes](../reference/exit-codes.md#contract-coverage-contribution-adr-049) |
| Aggregate schema `1.2`/`1.3` | A JSON consumer with a fixed field list will silently ignore the new `finding_matrix`/`profile_matrix`/`contract_coverage` blocks (additive, not a breaking JSON change) — but a consumer asserting an *exact* key set will fail | Check `aggregate_schema_version` if you need to react to the new blocks; see [Aggregate Reports](../use/aggregate-reports.md) |
| Scoped severity correction | A `compare --used-by`/`--required-symbol` script that assumed `--severity-*` flags were silently ignored on the scoped path now gets real severity-aware exit codes there too | Re-check any script relying on the old fixed legacy `0`/`2`/`4` mapping for a scoped run — see [Application Compatibility → Exit codes](../use/appcompat.md#exit-codes) |
| Project profiles becoming load-bearing | A `.abicheck.yml` `profiles:` block written before recent phases used `os`/`dependency_source`/`compile.frontend` as inert documentation; they now actually schedule the runner, provision dependencies, and select the AST frontend for that profile's cell | Review your `profiles:` block against [Scenario S17](../integration/scenarios/multi-platform.md) if you rely on a specific runner/toolchain per profile — an already-correct declaration needs no changes, but a previously-inert one now takes effect |
| MCP server removed | The `abicheck-mcp` executable, the `abicheck[mcp]` optional-dependency group, and every importable `abicheck.mcp_*` module are gone. `pip install "abicheck[mcp]"` no longer fails outright — pip just warns that the distribution doesn't provide the `mcp` extra and installs the base package — but any subsequent `abicheck.mcp_*` import, the `abicheck-mcp` executable, or an `abi_compare`/`abi_dump`/`abi_scan`/`abi_deps`/`abi_aggregate`/`abi_project_validate`/`abi_project_plan` MCP tool call now fails outright. There is no replacement protocol server | Point agent/CI integrations at the `abicheck` CLI (with `--format json`/`--format sarif` for machine consumption) or the typed Python API (`from abicheck.service import ...`, see [Python API](../use/python-api.md)) — both expose the same resolution/classification pipeline the MCP tools called into. See the retired [ADR-021](../contribute/adr/021-mcp-security-model.md) for historical context |

## `CompareResult` instead of a tuple

`run_compare`/`run_compare_request` returned a bare
`tuple[DiffResult, AbiSnapshot, AbiSnapshot]` before 0.6. The new
`CompareResult` dataclass adds a fourth field (`suppression`) — this **does**
break a caller that unpacked the old return value positionally
(`result, old, new = run_compare(...)`), since a dataclass isn't a tuple at
all. What it *doesn't* break is attribute/keyword access to the three
original values (`result.diff`, `result.old_snapshot`,
`result.new_snapshot`) — which is exactly why future fields can be added to
`CompareResult` without a repeat of this migration, the way they couldn't be
added to a bare tuple:

```python
# Before
result, old_snapshot, new_snapshot = run_compare(...)

# After
result, old_snapshot, new_snapshot = run_compare(...).as_tuple()
```

Full detail: [Python API → Compare two
libraries](../use/python-api.md#compare-two-libraries).

## Authoritative contract evaluation

If you already had `--contract` in a script or CI job before
this landed, re-read [Contract-Aware
Compatibility](../learn/contract-aware-compatibility.md) — relevance used
to be computed and reported but never consulted by policy (a shadow
annotation); it is now what decides whether a finding reaches policy at
all. This is the one item on this page that can genuinely change a
verdict for an *already opted-in* run — not for a plain `compare` without
the flag, which is unaffected either way.

## Contract-coverage exit `1`

A **new**, independent reason for exit `1` under `--contract`:
incomplete evidence for the selected domain. It's folded with `max` against
the ordinary severity gate, so it only ever raises a clean `0`, never lowers
a `2`/`4`. A CI script that branches only on `exit_code == 1` meaning
"severity error" should read the `compare` report's
`contract_coverage_exit_contribution` field (or inspect
`contract_coverage_failures`) to tell the two apart. See
[Exit Codes](../reference/exit-codes.md#contract-coverage-contribution-adr-049).

## Aggregate schema `1.2`/`1.3`

- `1.2` added `finding_matrix` (reconciling one finding across compiler
  profiles).
- `1.3` added the top-level `contract_coverage` block and a
  `contract_coverage_exit` field per target.

Both are purely additive — an existing JSON consumer reading only
`gate`/`coverage`/`compatibility`/`targets` needs no changes. See
[Aggregate Reports](../use/aggregate-reports.md) for what the new blocks
mean.

## Scoped severity correction

`compare --used-by`/`--required-symbol(s)` used to always compute its exit
code from a fixed legacy `0`/`2`/`4` mapping, regardless of any
`--severity-*` flag passed alongside it. That's fixed: a scoped run now
resolves the same `legacy`/`severity` scheme plain `compare` does (at the
time, an explicit `--exit-code-scheme` flag could still pin one regardless
of severity flags — that manual selector was removed in a later release; the
scheme is now purely automatic, see [CI Gating → the two exit-code
schemes](../use/ci-gating.md#the-two-exit-code-schemes)). If a script
depended on the old, silently-ignored behavior, re-check it against
[Application Compatibility → Exit codes](../use/appcompat.md#exit-codes).

## Project profiles

`.abicheck.yml`'s `profiles:` block gained real teeth: `os`/`dependency_source`
now schedule where a profile's check cell actually runs and how it
provisions dependencies, and `compile.frontend` actually steers a normal
(non-bundle) target cell's AST frontend end to end (not just documentation
of intent) — a `kind: bundle` check is the one exception, where it's still
projected but not applied, since a directory/package operand rejects any
non-`auto` frontend. If your `profiles:` block already declared these
correctly, nothing changes; if it declared them as inert notes (e.g. relying
on a workflow-level default to override them), review [Scenario
S17](../integration/scenarios/multi-platform.md) — they're load-bearing
now.

## See also

- `CHANGELOG.md` — the exhaustive, fragment-by-fragment record
- [Python API](../use/python-api.md) — the typed request API and result types
- [Contract-Aware Compatibility](../learn/contract-aware-compatibility.md)
- [Aggregate Reports](../use/aggregate-reports.md)
