#ifndef UNITS_H
#define UNITS_H

namespace units {

double to_celsius(double fahrenheit);

/* NEW in v2: a float overload ("avoid the double round-trip on
   embedded targets"). Binary-compatible — the double symbol is
   untouched — but every existing call site that passes a float
   silently re-routes to this overload on its next recompile, and
   `&units::to_celsius` is now ambiguous. */
float to_celsius(float fahrenheit);

} // namespace units

#endif
