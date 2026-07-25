# ADR-021b: MCP Security Model

**Date:** 2026-03-24
**Status:** Accepted — implemented
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

### Evidence (verified against the current implementation)

| Decision | Code | Test evidence |
|----------|------|----------------|
| D1: stdio-only transport | `mcp_server.py:1242` — `mcp.run(transport="stdio")`; no SSE/HTTP transport exists in this codebase, so the loopback-binding / `--auth-token` bullets remain forward-looking (nothing to verify yet) | — |
| D2: operation timeouts | `mcp_server.py:91` `MCP_TIMEOUT` (default 120s, `ABICHECK_MCP_TIMEOUT` / `--timeout`); enforced via `future.result(timeout=MCP_TIMEOUT)` at `mcp_server.py:496` (`abi_dump`), `:684` (`abi_compare`), `:950` (`abi_audit`), and `:1169` (`abi_scan`, via `run_scan_subprocess`) | `tests/test_mcp_server_coverage_gaps.py::TestAbiDump::test_abi_dump_timeout`, `::TestAbiCompare::test_abi_compare_timeout`, plus `test_timeout_branch` (x2), `test_nonpositive_timeout_errors` |
| D3: input file size limits | `mcp_server.py:94` `MCP_MAX_FILE_SIZE` (default 500 MB, `ABICHECK_MCP_MAX_FILE_SIZE` / `--max-file-size`); enforced by `_check_file_size()` at `mcp_server.py:100`, called before every resolve at lines 477, 598-599, 933, 1118, 1143, 1146 | `tests/test_mcp_server_coverage_gaps.py::test_check_file_size_over_limit_raises`, `::test_check_file_size_missing_is_noop`, `::test_check_file_size_stat_oserror_wrapped`, `::test_nonpositive_max_file_size_errors` |
| D4: audit logging | `_audit_log()` at `mcp_server.py:115`, called at every tool exit (success/error/timeout); `--log-format json` toggles `_structured_logging`; handler redirected to stderr at `mcp_server.py:1233` | `tests/test_mcp_hardening.py::TestAuditLog::test_text_format`, `::test_json_format` |
| Path safety (`_safe_write_path`, threat-model row) | `mcp_server.py:172` — extension whitelist, system-dir blocklist, credential-dir blocklist (`~/.ssh`, `~/.aws`, `~/.gnupg`), `Path.resolve()` symlink defeat | `tests/test_mcp_server_unit.py::TestSafeWritePath` (extension checks), `::test_credential_dir_blocked`, `::TestSafeWritePathTraversalEdgeCases::test_traversal_into_etc`, `tests/test_mcp_server_coverage.py::TestSafeWritePathWindows`, `::TestSafeWritePathResolveError` |
| Error sanitization (`_sanitize_error`) | `mcp_server.py:258` | `tests/test_mcp_server_unit.py::TestSanitizeError`, `::TestSanitizeErrorEdgeCases`, `tests/test_mcp_server_coverage_gaps.py::test_exception_branch_is_sanitized` (x2), `::test_unresolvable_input_returns_sanitized_error` |

All four D-series controls (D1-D4) and the threat-model mitigations are
implemented as of this writing — no gaps found during this verification
pass.
