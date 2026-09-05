#pragma once

namespace netcfg {

// A public API with a defaulted timeout. Callers rely on `connect(host)`.
int connect(const char *host, int timeout_ms = 5000);

}  // namespace netcfg
