// case115 v2 — same conditional declaration shape preserved. The
// "change" between v1 and v2 is symbolic: v2 makes `lib::extended`
// available *only* under USE_FEATURE — so consumers who do not define
// the macro now see a strictly smaller surface than they did in v1
// (where extended() was always present).
//
// The macro-conditioned shape is what the matrix detector flags as
// API_DEPENDS_ON_CONSUMER_ENV when it sees both macro states. The
// single-config example here serves as a fixture for the unit tests.
#pragma once

namespace lib {

void basic();

#ifdef USE_FEATURE
void extended();
#endif

} // namespace lib
