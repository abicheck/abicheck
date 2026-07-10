/* DEMO: no runtime signal — that is the point of this case. The app,
   compiled against v1, calls to_celsius(98.6f); the float argument
   promotes to double and binds to the only overload. The old binary
   keeps working against libv2 (the double symbol is unchanged).
   The risk fires at the NEXT RECOMPILE: against v2 headers the same
   call silently re-routes to the new float overload (different
   precision), and `&units::to_celsius` becomes ambiguous. */
#include "v1.h"
#include <cmath>
#include <cstdio>

int main() {
    double c = units::to_celsius(98.6f);
    std::printf("to_celsius(98.6f) = %.4f (expected 37.0)\n", c);
    return std::fabs(c - 37.0) > 0.001;
}
