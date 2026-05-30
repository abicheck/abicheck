// case110 v2 — experimental::bar deleted; no stable lib::bar published.
//
// This is the unfriendly removal pattern: the experimental feature
// disappeared and there is no migration target. Consumers of the
// experimental name fail to compile against v2.
//
// An *unrelated* function is kept so the v2 library still produces a
// shared object — without exported symbols some platforms refuse to
// link, which would mask the signal we want the detector to catch.
#pragma once

namespace lib {

void unrelated();

} // namespace lib
