# vecmath
Header-heavy C++17 math library: most of the API is inline templates,
`constexpr` functions and macros in `include/vecmath/`, plus a small shared
library. Tagged GitHub Releases (`vX.Y.Z`).

Last quarter we broke users twice without changing a single exported symbol:
once by changing a default argument, once by changing `VECMATH_DEFAULT_ALIGN`.
