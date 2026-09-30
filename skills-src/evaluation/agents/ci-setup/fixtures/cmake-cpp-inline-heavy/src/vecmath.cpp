#include "vecmath/vecmath.hpp"
#include <cmath>
namespace vecmath { double norm3(const Vec<double, 3> &a) { return std::sqrt(dot(a, a)); } }
