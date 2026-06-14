# MCP Server (Agent Integration)

abicheck includes an MCP ([Model Context Protocol](https://modelcontextprotocol.io/)) server that exposes ABI checking as structured tools for AI agents — Claude Code, Cursor, VS Code Copilot, OpenAI Agents, and any other MCP-compatible client.

## Install

```bash
pip install "abicheck[mcp]"
```

Or with conda:

```bash
conda install abicheck
pip install "mcp[cli]>=1.2.0"
```

> The `mcp` dependency is optional. The base `pip install abicheck` does not pull it in.

## Configure

### Claude Desktop / Claude Code

Add to `~/.claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "abicheck": {
      "command": "abicheck-mcp"
    }
  }
}
```

### Cursor / VS Code

Add to `.cursor/mcp.json` or VS Code MCP settings:

```json
{
  "mcpServers": {
    "abicheck": {
      "command": "abicheck-mcp"
    }
  }
}
```

### Without installing (via uv)

```json
{
  "mcpServers": {
    "abicheck": {
      "command": "uv",
      "args": ["--directory", "/path/to/abicheck", "run", "abicheck-mcp"]
    }
  }
}
```

## Tools

The MCP server exposes four tools. All return JSON-encoded strings.

**Response envelopes.** On failure, every tool returns the same error
envelope: `{"status": "error", "error": "<message>"}`. On success the
shape differs by tool:

| Tool | Success envelope |
|---|---|
| `abi_compare` | `{"status": "ok", "verdict": ..., ...}` |
| `abi_dump` | `{"status": "ok", "summary": ..., ...}` |
| `abi_list_changes` | `{"count": ..., "change_kinds": [...]}` — no `status` field |
| `abi_explain_change` | `{"kind": ..., "impact": ..., ...}` — no `status` field |

The simplest client check is: `status == "error"` ⇒ failure; otherwise
treat as success and parse the tool-specific payload. See
[Error responses](#error-responses) for the error shape and common causes.

### `abi_compare` — Compare two ABI surfaces

The primary tool. Diffs two library versions and reports breaking changes.

Each input is auto-detected and may be a shared library
(`.so` / `.dll` / `.dylib`), a JSON snapshot produced by `abi_dump`, or an
ABICC Perl dump (`.pl` / `.dump`).

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `old_input` | string | yes | Path to old `.so`/`.dll`/`.dylib`, JSON snapshot, or ABICC Perl dump |
| `new_input` | string | yes | Path to new `.so`/`.dll`/`.dylib`, JSON snapshot, or ABICC Perl dump |
| `headers` | string[] | no | Shared header file list applied to both sides. Overridden per-side by `old_headers` / `new_headers` when those are supplied |
| `old_headers` | string[] | no | Headers for old side only. Takes precedence over `headers` |
| `new_headers` | string[] | no | Headers for new side only. Takes precedence over `headers` |
| `include_dirs` | string[] | no | Include directories for the C/C++ parser |
| `language` | string | no | `"c++"` (default) or `"c"` |
| `policy` | string | no | `"strict_abi"` (default), `"sdk_vendor"`, or `"plugin_abi"`. See [Policy Profiles](policies.md) |
| `policy_file` | string | no | Path to custom YAML policy file. Overrides `policy` when set |
| `suppression_file` | string | no | Path to YAML [suppression](suppressions.md) file |
| `output_format` | string | no | Report format: `"json"` (default), `"markdown"`, `"sarif"`, `"html"` |
| `show_only` | string | no | Comma-separated filter tokens (display only). Severity: `breaking`, `api-break`, `risk`, `compatible`. Element: `functions`, `variables`, `types`, `enums`, `elf`. Action: `added`, `removed`, `changed` |
| `report_mode` | string | no | `"full"` (default) or `"leaf"` (root-type-grouped view) |
| `show_impact` | boolean | no | If `true`, append an impact summary table to the rendered report |
| `stat` | boolean | no | If `true`, emit a one-line summary instead of the full report |

**Response fields:**

```json
{
  "status": "ok",
  "verdict": "BREAKING",
  "exit_code": 4,
  "summary": {
    "breaking": 2,
    "api_breaks": 0,
    "risk_changes": 0,
    "compatible": 1,
    "total_changes": 3
  },
  "changes": [
    {
      "kind": "func_removed",
      "symbol": "_Z6helperv",
      "description": "Public function removed: helper",
      "impact": "breaking",
      "old_value": "helper",
      "new_value": null,
      "source_location": "include/foo.h:42"
    }
  ],
  "suppressed_count": 0,
  "report": "..."
}
```

- `verdict` is one of: `NO_CHANGE`, `COMPATIBLE`, `COMPATIBLE_WITH_RISK`,
  `API_BREAK`, `BREAKING`. See [Verdicts](../concepts/verdicts.md).
- `changes[].impact` is one of `breaking`, `api_break`, `risk`, `compatible`
  and reflects the *active* policy (so `sdk_vendor` may downgrade
  source-level renames from `api_break` to `compatible`).
- `changes[].source_location` is the originating header coordinate
  (`"header.h:42"`) when known, otherwise `null`.
- `report` is the rendered report. For `output_format="json"` it is embedded
  as a nested object; for `"markdown"`, `"sarif"`, and `"html"` it is a
  string.

**Verdict → `exit_code` mapping** (matches the CLI):

| Verdict | `exit_code` |
|---|:---:|
| `NO_CHANGE` | 0 |
| `COMPATIBLE` | 0 |
| `COMPATIBLE_WITH_RISK` | 0 |
| `API_BREAK` | 2 |
| `BREAKING` | 4 |

See [Exit Codes](../reference/exit-codes.md) for the full CLI matrix.

---

### `abi_dump` — Extract ABI snapshot

Extracts the public ABI surface from a shared library (and, for ELF, its
headers) into a JSON snapshot.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `library_path` | string | yes | Path to `.so`/`.dll`/`.dylib` file |
| `headers` | string[] | no | Public header file paths. For ELF (`.so`), omitting headers produces a **symbol-only** snapshot with no type information. Not used for PE (`.dll`) or Mach-O (`.dylib`) inputs |
| `include_dirs` | string[] | no | Extra include directories for the C/C++ parser |
| `version` | string | no | Version label embedded in the snapshot (default: `"unknown"`) |
| `language` | string | no | `"c++"` (default) or `"c"` |
| `output_path` | string | no | If provided, write the snapshot to this file; otherwise the snapshot is returned inline. See [Path restrictions](#path-restrictions-for-output_path) |

**Response — inline snapshot** (no `output_path`):

```json
{
  "status": "ok",
  "summary": {
    "library": "libfoo.so.1",
    "version": "1.2.3",
    "platform": "elf",
    "functions": 42,
    "variables": 3,
    "types": 12,
    "enums": 5
  },
  "snapshot": { "...": "..." }
}
```

**Response — snapshot written to disk** (with `output_path`):

```json
{
  "status": "ok",
  "output_path": "/abs/path/to/snapshot.json",
  "summary": {
    "library": "libfoo.so.1",
    "version": "1.2.3",
    "platform": "elf",
    "functions": 42,
    "variables": 3,
    "types": 12,
    "enums": 5
  }
}
```

The `snapshot` field is omitted when `output_path` is used — read the file
from disk instead.

---

### `abi_list_changes` — List detectable change kinds

Enumerates all 250 `ChangeKind` values with their impact classification. See
the [Change Kinds Reference](../reference/change-kinds.md) for canonical
documentation of each kind.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `impact` | string | no | Filter: `"breaking"`, `"api_break"`, `"risk"`, or `"compatible"` |

**Response:**

```json
{
  "count": 250,
  "change_kinds": [
    {
      "kind": "func_removed",
      "impact": "breaking",
      "default_verdict": "BREAKING",
      "description": "Old binaries call a symbol that no longer exists..."
    }
  ]
}
```

---

### `abi_explain_change` — Explain a specific change kind

Returns a detailed explanation of what a change kind means, why it's dangerous, and how to fix it.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `change_kind` | string | yes | e.g. `"func_removed"`, `"type_size_changed"` |

**Response:**

```json
{
  "kind": "type_size_changed",
  "impact": "breaking",
  "default_verdict": "BREAKING",
  "severity": "error",
  "description": "Old code allocates or copies the type with the old size; heap/stack corruption, out-of-bounds access.",
  "fix_guidance": "This is a binary ABI break. Options: (1) revert the change, (2) bump the SONAME/major version, (3) add the old symbol as a compatibility alias."
}
```

The `severity` field is either `"error"` (`BREAKING`) or `"warning"`
(`API_BREAK`, `COMPATIBLE_WITH_RISK`, and `COMPATIBLE` kinds).

## Agent workflow examples

### Check a PR for ABI compatibility

```text
Agent: "Check if this PR breaks ABI"

1. abi_compare(old_input="baseline.json", new_input="build/libfoo.so",
               new_headers=["include/foo.h"])
   → verdict: BREAKING, changes: [{kind: "func_removed", ...}]

2. abi_explain_change(change_kind="func_removed")
   → "Old binaries call a symbol that no longer exists..."

3. Agent posts PR comment with findings and fix suggestions
```

### Developer asks "Is my struct change safe?"

```text
User: "I added a field to FooConfig — is this ABI safe?"

1. abi_compare(old_input="old.json", new_input="new.json")
   → verdict: BREAKING, changes: [{kind: "type_size_changed", ...}]

2. abi_explain_change(change_kind="type_size_changed")
   → "Old code allocates the type with the old size; heap/stack corruption."

3. Agent suggests using a pointer to an opaque extension struct instead
```

### Explore what abicheck detects

```text
Agent: "What kinds of ABI breaks can you detect?"

1. abi_list_changes(impact="breaking")
   → the breaking subset of the change-kind registry, with descriptions

2. Agent summarizes the categories for the user
```

## Error responses

Every tool returns a JSON-encoded string. When something goes wrong the
envelope is:

```json
{
  "status": "error",
  "error": "<short, sanitized message>"
}
```

`error` messages from `AbicheckError`, `ValueError`, and `KeyError` are
surfaced verbatim. Errors arising from the host file system
(`OSError` / `FileNotFoundError` / `PermissionError`) and unexpected
exceptions are reduced to generic messages — full details are logged to
stderr but never returned to the MCP client. This avoids leaking absolute
paths or internals through the agent transcript.

Common causes:

| Cause | Example `error` field |
|---|---|
| Missing input | `"File not found for old_input"` |
| File too large | `"old_input is 612.4 MB, exceeds limit of 500 MB"` |
| Tool exceeded `--timeout` | `"abi_compare timed out after 120s"` |
| Unknown policy | `"Unknown policy: 'foo'. Valid policies: plugin_abi, sdk_vendor, strict_abi"` |
| Bad `output_format` | `"Unknown output format 'pdf'. Valid: ['html', 'json', 'markdown', 'sarif']"` |
| Bad `show_only` | `"Invalid show_only: <token>"` |
| Disallowed `output_path` | `"output_path must have a .json extension, got: '.txt'"` |
| Unrecognized change kind | `"Unknown change kind: 'foo'. Use abi_list_changes to see all available kinds."` |
| Unparseable input | `"Cannot detect input format. Expected: ELF (.so), PE (.dll), Mach-O (.dylib), JSON snapshot, or ABICC Perl dump."` |

The MCP server process keeps running on errors — only the failing tool
invocation is terminated.

## Runtime configuration

`abicheck-mcp` accepts CLI flags and environment variables for resource
limits and logging. Flags override environment variables; environment
variables override defaults.

| CLI flag | Environment variable | Default | Purpose |
|---|---|---|---|
| `--timeout <s>` | `ABICHECK_MCP_TIMEOUT` | `120` | Per-call timeout (seconds) for `abi_dump` and `abi_compare`. On timeout the tool returns a structured error; the server stays up |
| `--max-file-size <bytes>` | `ABICHECK_MCP_MAX_FILE_SIZE` | `524288000` (500 MB) | Maximum size of any input file (`library_path`, `old_input`, `new_input`) |
| `--log-format text\|json` | — | `text` | Audit log format on stderr |

Example invocation tuned for large libraries:

```bash
ABICHECK_MCP_TIMEOUT=600 ABICHECK_MCP_MAX_FILE_SIZE=2147483648 \
  abicheck-mcp --log-format json
```

Or via MCP config:

```json
{
  "mcpServers": {
    "abicheck": {
      "command": "abicheck-mcp",
      "args": ["--timeout", "600", "--log-format", "json"],
      "env": {
        "ABICHECK_MCP_MAX_FILE_SIZE": "2147483648"
      }
    }
  }
}
```

### Audit logging

Every tool invocation is logged at `INFO` level to **stderr** (stdout is
reserved for JSON-RPC). The default text format looks like:

```text
INFO: abicheck.mcp: tool=abi_compare old=libfoo_v1.so new=libfoo_v2.so duration=3.412s status=ok verdict=BREAKING
```

With `--log-format json`:

```json
{"tool": "abi_compare", "inputs": {"old": "libfoo_v1.so", "new": "libfoo_v2.so"}, "duration_s": 3.412, "status": "ok", "verdict": "BREAKING"}
```

Only basenames are logged — never absolute paths. `status` is one of `ok`,
`timeout`, or `error`. See [ADR-021b](../development/adr/021-mcp-security-model.md)
for the audit-logging rationale.

## Troubleshooting

**The MCP client can't find `abicheck-mcp`.**
The optional `[mcp]` extra is required: `pip install "abicheck[mcp]"`. The
plain `pip install abicheck` does not pull `mcp` in. With conda:
`conda install abicheck && pip install "mcp[cli]>=1.2.0"`.

**Tool calls silently fail with no logs.**
Logs go to **stderr**, not stdout. If you launched the server through an
MCP client, capture its stderr — most clients expose this in a "server
output" or "logs" panel. Stdout must remain pure JSON-RPC; anything written
there will corrupt the transport.

**`abi_compare` returns `timed out after 120s`.**
Large libraries with full DWARF can exceed the default. Raise
`--timeout` (or `ABICHECK_MCP_TIMEOUT`). 600 s is a reasonable ceiling for
multi-hundred-MB libraries. Use `abi_dump` to materialise snapshots once
and compare snapshots thereafter — snapshot-to-snapshot compares are far
faster than binary-to-binary.

**`abi_dump` on a `.so` returns very few `types`/`enums`.**
You probably didn't supply `headers`. Without headers, ELF dumps degrade
to a symbol-only snapshot. Add the public headers (and `include_dirs` if
they include third-party headers).

**`Cannot detect input format`.**
The file is not an ELF/PE/Mach-O binary, a JSON snapshot, or an ABICC
Perl dump. Check the file with `file <path>` and convert if needed.

**`output_path` is rejected.**
Only `.json` outputs are allowed, and the path must not resolve into
system or credential directories. Write to a project-local directory or
a temp dir.

## Transport

The server uses **stdio** transport — the agent spawns `abicheck-mcp` as a local subprocess and communicates over stdin/stdout. No network, no ports, no deployment needed.

## Architecture

```text
┌──────────────────────────────────┐
│   MCP Client (Agent / IDE)       │
│   Claude Code, Cursor, VS Code   │
└──────────┬───────────────────────┘
           │ stdio (JSON-RPC)
           ▼
┌──────────────────────────────────┐
│   abicheck-mcp                   │
│   abicheck/mcp_server.py         │
│                                  │
│   Tools:                         │
│     abi_dump                     │
│     abi_compare                  │
│     abi_list_changes             │
│     abi_explain_change           │
└──────────┬───────────────────────┘
           │ Python imports
           ▼
┌──────────────────────────────────┐
│   abicheck core library          │
│   dumper / checker / reporter    │
└──────────────────────────────────┘
```

## Security

See [ADR-021b](../development/adr/021-mcp-security-model.md) for the full
threat model and design rationale.

### Path restrictions for `output_path`

When `abi_dump` writes a snapshot to disk via `output_path`, the MCP server
enforces the following policy:

- **Extension**: only `.json` files are allowed
- **System directories**: writes to `/etc`, `/bin`, `/sbin`, `/usr/bin`,
  `/usr/sbin`, `/boot`, `/sys`, `/proc`, `/dev` are blocked on Linux/macOS;
  `C:\Windows\`, `C:\System32\` etc. are blocked on Windows
- **Credential directories**: `~/.ssh`, `~/.aws`, `~/.gnupg` are always blocked
- **Symlink-safe**: resolved paths are used for comparison to prevent traversal
  via `../../etc/` or `//etc/` bypasses

Read paths (`library_path`, `headers`, `include_dirs`) are not restricted —
they follow the same access controls as the user running the MCP server.

### Read-side protections

In addition to the write-path policy above, every tool call is bounded
to keep one bad input from disabling the server:

- **File-size cap**: input files larger than `--max-file-size` (default
  500 MB) are rejected before any parsing begins.
- **Per-call timeout**: `abi_dump` and `abi_compare` run in a worker
  thread bounded by `--timeout` (default 120 s). On timeout the tool
  returns a structured error and the server keeps serving subsequent
  calls.
- **Sanitized errors**: filesystem paths and internal stack frames are
  scrubbed from error messages returned to the client. Full details
  remain in stderr logs for operators.

### Transport

The default stdio transport has no network listener — the MCP client
spawns `abicheck-mcp` as a local subprocess and inherits the user's
filesystem permissions. There is no authentication layer because there is
no remote surface. Do not expose the server over the network without
adding loopback-only binding and bearer-token auth (see ADR-021b).

## Related documentation

- [Verdicts](../concepts/verdicts.md) — what each `verdict` value means
- [Change Kinds Reference](../reference/change-kinds.md) — canonical list
  of values for `change_kind` and `changes[].kind`
- [Exit Codes](../reference/exit-codes.md) — full CLI exit-code matrix
- [Policy Profiles](policies.md) — `strict_abi`, `sdk_vendor`,
  `plugin_abi`, and custom YAML policies
- [Suppressions](suppressions.md) — YAML and ABICC-format suppression
  files accepted by `suppression_file`
- [Output Formats](output-formats.md) — JSON, Markdown, SARIF, HTML
- [Architecture](../concepts/architecture.md) — where the MCP server
  sits in the overall pipeline
- [ADR-021b: MCP Security Model](../development/adr/021-mcp-security-model.md)
