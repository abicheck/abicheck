#pragma once

namespace netcfg {

// v2 removes the default. The mangled symbol is identical (defaults don't
// affect mangling), so existing binaries keep linking and running — but any
// source that called `connect(host)` no longer compiles. A pure source/API
// break, invisible to object/DWARF comparison.
int connect(const char *host, int timeout_ms);

}  // namespace netcfg
