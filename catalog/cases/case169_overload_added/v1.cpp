#include "v1.h"

namespace units {

double to_celsius(double fahrenheit) {
    return (fahrenheit - 32.0) * 5.0 / 9.0;
}

} // namespace units
