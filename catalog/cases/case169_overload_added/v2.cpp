#include "v2.h"

namespace units {

double to_celsius(double fahrenheit) {
    return (fahrenheit - 32.0) * 5.0 / 9.0;
}

float to_celsius(float fahrenheit) {
    return (fahrenheit - 32.0f) * 5.0f / 9.0f;
}

} // namespace units
