// case115 v1 — public API surface depends on the consumer's compile-
// time macro `USE_FEATURE`.
//
// When the consumer compiles with `-DUSE_FEATURE=1`, `lib::extended()`
// is declared; without it, only `lib::basic()` is visible. The library
// considers both shapes "public", but a consumer who happens to define
// the macro sees a strictly larger API than one who does not — and any
// program that links objects compiled under different macro values
// ends up with an inconsistent public surface.
//
// This is the failure mode the probe-harness matrix is designed to
// surface. The example ships a single configuration; the dedicated
// detector fires when abicheck is invoked with a `--probe-harness`
// manifest exposing both macro states.
#pragma once

namespace lib {

void basic();

#ifdef USE_FEATURE
void extended();
#endif

} // namespace lib
