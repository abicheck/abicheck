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

- `abicheck/mcp_server.py` — original four tools (`abi_dump`/`abi_compare`/
  `abi_audit`/`abi_scan`) plus MCP wiring
- `abicheck/mcp_server_project.py` — `abi_deps`/`abi_aggregate`/
  `abi_project_validate`/`abi_project_plan`, added after this ADR's initial
  verification pass (same D1-D4 controls, reusing the shared helpers below)
- `abicheck/mcp_shared.py` — `MCP_TIMEOUT`/`MCP_MAX_FILE_SIZE`/
  `_call_with_timeout`/`_check_file_size`/`_audit_log`/`_sanitize_error`,
  factored out of `mcp_server.py` into this leaf module so both tool modules
  read the same runtime-configurable values (see `docs/use/mcp-integration.md`
  for the current, canonical eight-tool coverage table — don't restate the
  per-tool list here, it drifts out of sync with that single source of truth)
- MCP specification: https://modelcontextprotocol.io/
- FastMCP: https://github.com/jlowin/fastmcp

### Evidence (verified against the current implementation)

| Decision | Code | Test evidence |
|----------|------|----------------|
| D1: stdio-only transport | `mcp_server.py:1242` — `mcp.run(transport="stdio")`; no SSE/HTTP transport exists in this codebase, so the loopback-binding / `--auth-token` bullets remain forward-looking (nothing to verify yet) | — |
| D2: operation timeouts | `mcp_shared.py`'s `MCP_TIMEOUT` (default 120s, `ABICHECK_MCP_TIMEOUT` / `--timeout`); enforced via `future.result(timeout=MCP_TIMEOUT)` in `mcp_server.py`'s `_call_with_timeout` (`abi_dump`/`abi_compare`/`abi_audit`) and `run_scan_subprocess` (`abi_scan`), and in `mcp_server_project.py`'s own `_call_with_timeout` (`abi_deps`/`abi_aggregate`/`abi_project_validate`/`abi_project_plan`) — two separate `_call_with_timeout` helpers exist, one per module, both reading the same shared `mcp_shared.MCP_TIMEOUT` | `tests/test_mcp_server_coverage_gaps.py::TestAbiDump::test_abi_dump_timeout`, `::TestAbiCompare::test_abi_compare_timeout`, plus `test_timeout_branch` (x2), `test_nonpositive_timeout_errors`; `tests/test_mcp_server_deps_aggregate_project.py`'s per-tool `test_timeout*` classes |
| D3: input file size limits | `mcp_shared.py`'s `MCP_MAX_FILE_SIZE` (default 500 MB, `ABICHECK_MCP_MAX_FILE_SIZE` / `--max-file-size`); enforced by `_check_file_size()` (also in `mcp_shared.py`), called before every resolve in both `mcp_server.py` and `mcp_server_project.py` | `tests/test_mcp_server_coverage_gaps.py::test_check_file_size_over_limit_raises`, `::test_check_file_size_missing_is_noop`, `::test_check_file_size_stat_oserror_wrapped`, `::test_nonpositive_max_file_size_errors`; `tests/test_mcp_server_deps_aggregate_project.py`'s per-tool `test_oversized*` tests |
| D4: audit logging | `_audit_log()` in `mcp_shared.py`, called at every tool exit (success/error/timeout) across both tool modules; `--log-format json` toggles `_structured_logging`; handler redirected to stderr in `mcp_server.py`'s `main()` | `tests/test_mcp_hardening.py::TestAuditLog::test_text_format`, `::test_json_format`; `tests/test_mcp_server_deps_aggregate_project.py`'s per-tool `test_*_is_audit_logged` tests |
| Path safety (`_safe_write_path`, threat-model row) | `mcp_server.py:172` — extension whitelist, system-dir blocklist, credential-dir blocklist (`~/.ssh`, `~/.aws`, `~/.gnupg`), `Path.resolve()` symlink defeat | `tests/test_mcp_server_unit.py::TestSafeWritePath` (extension checks), `::test_credential_dir_blocked`, `::TestSafeWritePathTraversalEdgeCases::test_traversal_into_etc`, `tests/test_mcp_server_coverage.py::TestSafeWritePathWindows`, `::TestSafeWritePathResolveError` |
| Error sanitization (`_sanitize_error`) | `mcp_shared.py` (moved from `mcp_server.py` in the split that added `mcp_server_project.py`) | `tests/test_mcp_server_unit.py::TestSanitizeError`, `::TestSanitizeErrorEdgeCases`, `tests/test_mcp_server_coverage_gaps.py::test_exception_branch_is_sanitized` (x2), `::test_unresolvable_input_returns_sanitized_error` |

All four D-series controls (D1-D4) and the threat-model mitigations are
implemented as of this writing — no gaps found during this verification
pass. **Caveat carried over from `mcp-integration.md`'s own "Read-side
protections" section**: D2's timeout guarantee is per-call-response, not
per-process-resource — a timed-out worker thread (every bounded tool except
`abi_scan`, which uses a killable subprocess) is not actually killed, since
Python has no API to forcibly stop a running thread; it keeps running in the
background until it finishes on its own. This is a known, accepted trade-off
(`wait=False` avoids blocking the *caller* past the timeout, at the cost of
not reclaiming the *worker*'s resources immediately) — not a gap in this
verification pass, but worth stating plainly here rather than only implying
it via the D2 code reference above.
