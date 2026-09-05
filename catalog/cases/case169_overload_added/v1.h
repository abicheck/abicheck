#ifndef UNITS_H
#define UNITS_H

namespace units {

/* v1: exactly one to_celsius() — every call, whatever the argument
   type, converts through double. */
double to_celsius(double fahrenheit);

} // namespace units

#endif
