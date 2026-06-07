# ADR-021b: MCP Security Model

**Date:** 2026-03-24
**Status:** Accepted
**Decision maker:** Nikolay Petrov

---

## Context

The abicheck MCP server exposes `abi_dump`, `abi_compare`, `abi_list_changes`, and
`abi_explain_change` as MCP tools. These tools read arbitrary binary files, parse
headers with castxml (a C/C++ compiler wrapper), and optionally write JSON output
files. Security considerations:

1. **Transport:** Currently stdio-only (JSON-RPC over stdin/stdout). The process
   inherits the caller's permissions. No network listener exists.

2. **Path safety:** `_safe_write_path` enforces:
   - Extension whitelist (`.json` only)
   - System directory blocklist (`/etc`, `/bin`, `/usr/sbin`, etc.)
   - Credential directory blocklist (`~/.ssh`, `~/.aws`, `~/.gnupg`)
   - Symlink resolution to defeat traversal

3. **Error sanitization:** `_sanitize_error` strips filesystem paths from error
   messages returned to the MCP client, preventing information leakage.

4. **No authentication:** stdio transport inherits process-level access. The MCP
   client (Claude Code, Cursor, etc.) is trusted as the local user.

### Threat model

| Threat | Mitigation | Status |
|--------|-----------|--------|
| Arbitrary file write | Extension + directory blocklist | Implemented |
| Path traversal via symlinks | `Path.resolve()` before all checks | Implemented |
| Error message leakage | `_sanitize_error` strips paths | Implemented |
| Denial of service (huge binary) | None | **Gap** |
| Long-running castxml hang | None | **Gap** |
| Unauthorized remote access | stdio-only (no listener) | Implemented |
| Prompt injection via file content | MCP tool output is structured JSON | Mitigated |

## Decision

### D1: stdio-only transport remains the default

The stdio transport is a deliberate security choice. The MCP server MUST NOT bind
to a network port by default. If a networked mode (SSE/HTTP) is added in the future:

- Bind to `127.0.0.1` only (loopback enforcement)
- Require `--auth-token` flag for Bearer token validation
- Emit a warning if `--transport sse` is used without `--auth-token`

### D2: Operation timeouts

All tool invocations MUST have a configurable timeout:

- Default: 120 seconds for `abi_dump` and `abi_compare`
- Configurable via `--timeout` CLI flag or `ABICHECK_MCP_TIMEOUT` env var
- On timeout: return structured error, do not kill the server

### D3: Input file size limits

Tool invocations MUST check input file size before processing:

- Default maximum: 500 MB per input file
- Configurable via `--max-file-size` CLI flag or `ABICHECK_MCP_MAX_FILE_SIZE` env var
- On exceed: return structured error with file size and limit

### D4: Audit logging

Every tool invocation MUST be logged at INFO level to stderr:

- Fields: tool name, input paths (basenames only), duration, verdict/status
- Structured JSON format available via `--log-format json`
- Logs go to stderr (never stdout — that's the JSON-RPC channel)

## Consequences

### Positive

- Timeouts prevent the server from hanging on malformed binaries
- File size limits prevent OOM on huge inputs
- Audit logging provides observability for debugging and compliance
- ADR documents security decisions for future contributors

### Negative

- Timeout defaults may need tuning for very large libraries (>100MB with DWARF)
- Structured logging adds a minor performance overhead (~1ms per invocation)

## References

- `abicheck/mcp_server.py` — implementation
- MCP specification: https://modelcontextprotocol.io/
- FastMCP: https://github.com/jlowin/fastmcp
