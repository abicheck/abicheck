# Companion Commands: Surface, Source-Graph, and PR-Comment Reports

These commands report on a single library's public surface, the L5 source
graph, or an existing report — they never produce a compare verdict or affect
the `compare` exit code.

> Split out of [CLI Usage](cli-usage.md), which covers the core `dump`/
> `compare` flow.

## `surface-report` — public-surface structural metrics

Emits descriptive structural facts about **one** library's public ABI surface
(no diff): header→symbol coverage, undocumented-export ratio, type fan-in, and
per-header cohesion. Purely descriptive — it never computes a verdict.

```
abicheck surface-report [OPTIONS] LIBRARY
```

`LIBRARY` is a shared library (ELF/PE/Mach-O) or an `.abi.json` snapshot.

| Flag | Value | Default | Purpose |
|------|-------|---------|---------|
| `-H` / `--header` | path (repeatable) | none | Public header file or directory (repeatable). Enables header-aware coverage metrics. |
| `-I` / `--include` | path (repeatable) | none | Additional include directory passed to the header parser. |
| `--format` | `text` \| `json` | `text` | Output format. |
| `--top` | integer ≥ 1 | `10` | How many highest-fan-in types to list. |
| `--idioms` / `--no-idioms` | flag | `--no-idioms` | Recognise and report API idioms (opaque pointer, PIMPL, handle, factory, create/destroy, callback). |
| `--anti-patterns` / `--no-anti-patterns` | flag | `--no-anti-patterns` | Detect and report ABI anti-patterns (`std::` types crossed by value, polymorphic types with no virtual destructor). |
| `--audit` / `--no-audit` | flag | `--no-audit` | Run the single-release hygiene audit (intra-version cross-source checks: accidental ABI surface, private-header leaks, unversioned exports, RTTI for internal types, …). No baseline needed; advisory only. |
| `-o` / `--output` | path | stdout | Write report to a file. |

```bash
# Structural metrics + idiom/anti-pattern report for one library's public surface
abicheck surface-report libfoo.so -H include/ --idioms --anti-patterns

# JSON output to a file
abicheck surface-report libfoo.so -H include/ --format json -o surface.json

# Single-release hygiene lint (no baseline)
abicheck surface-report libfoo.so -H include/ --audit
```

See [API Surface Intelligence](api-surface-intelligence.md) for what
the surface metrics, idiom recognizers, and anti-pattern checks mean.

## `graph compare` — structural source-graph diff

Compares two L5 source-graph summaries and reports which nodes/edges entered or
left the graph. The diff *explains and prioritizes* impact; it never, on its
own, decides or suppresses an artifact-proven ABI break.

```
abicheck graph compare [OPTIONS] OLD NEW
```

`OLD` and `NEW` may each be a `graph/source_graph_summary.json` file or an
evidence-pack directory produced by `collect --source-graph summary`.

| Flag | Value | Default | Purpose |
|------|-------|---------|---------|
| `--format` | `text` \| `json` | `text` | Output format for the structural graph diff. |

```bash
# Diff two L5 source-graph summaries (from `collect --source-graph`)
abicheck graph compare old-pack/ new-pack/
```

## `graph explain` — localize a symbol through the source graph

Given an exported symbol (directly via `--symbol`, or resolved from a `compare`
JSON report finding via `--finding-id`), walks the graph to show what produced
and reaches it: exporting target, source declaration(s), declaring public
header(s), ABI-relevant build option(s), and static callees. This explains and
prioritizes; it is never an ABI verdict.

```
abicheck graph explain [OPTIONS]
```

| Flag | Value | Default | Purpose |
|------|-------|---------|---------|
| `--sources` | path (**required**) | — | Source/graph pack directory (or a `source_graph_summary.json`) to explain through. |
| `--symbol` | text | empty | Exported (mangled) binary symbol to localize. |
| `--report` | path | none | A `compare --format json` report; with `--finding-id`, resolves the symbol from it. |
| `--finding-id` | text | empty | Index (or symbol) of a finding in `--report` to localize. |
| `--format` | `text` \| `json` | `text` | Output format for the localization result. |

```bash
# Localize a specific symbol
abicheck graph explain --sources new-pack/ --symbol _ZN3foo3barEv

# Localize a compare finding by index (which TU/include chain produced it)
abicheck graph explain --report report.json --finding-id 0 --sources new-pack/
```

See [Build & Source Packs](../concepts/build-source-data.md) for producing the
packs that `graph compare` / `graph explain` consume.

## `pr-comment` — render a sticky GitHub PR comment

Renders a sticky GitHub PR-comment body from a JSON report produced by
`abicheck compare` or `abicheck appcompat` (`--format json`); `compare` also
covers release/bundle comparisons on directory/package inputs. When `--on=never`,
or `--on=changes` and the report has no changes, nothing is written (an empty
`--output` file is produced) so the caller can skip posting.

```
abicheck pr-comment [OPTIONS] REPORT
```

`REPORT` is a JSON file from `abicheck compare` or `abicheck appcompat` run with
`--format json` (release/bundle fan-out is handled by `compare` on directory/
package inputs — there is no separate `compare-release` command).

| Flag | Value | Default | Purpose |
|------|-------|---------|---------|
| `--sha` | text | empty | Commit SHA being scanned (PR head). |
| `--detail` | `summary` \| `standard` \| `full` | `standard` | How much per-change detail to include in the comment. |
| `--on` | `always` \| `changes` \| `never` | `changes` | When to emit a comment body: always, only on changes, or never. |
| `--run-label` | text | none | Run label shown in the footer, e.g. `run #128`. |
| `--report-url` | text | none | URL of the full report/run, linked in the footer and used when the comment is condensed or truncated to fit GitHub's size limit. |
| `--gate-api-break` | flag | off | Treat API/source breaks as breaking (mirror `fail-on-api-break`, which turns the check red on them). |
| `-o` / `--output` | path | stdout | Write the comment markdown to a file. |

```bash
# Produce a JSON report, then render a PR-comment body from it
abicheck compare old.json new.so -H include/ --format json -o report.json
abicheck pr-comment report.json --sha "$GITHUB_SHA" -o comment.md
```
