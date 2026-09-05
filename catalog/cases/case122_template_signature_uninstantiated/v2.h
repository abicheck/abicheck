#pragma once

// v2 changes the template's parameter types (int -> long). Any consumer
// expression like `clamp<int>(x, a, b)` resolves to a different signature and
// produces a different mangled symbol on the consumer side. A real source/ABI
// break for users of the template — yet the library binary is byte-identical,
// and castxml does not emit uninstantiated template declarations, so abicheck
// cannot see it in ANY mode.
template <typename T>
T clamp(T value, long lo, long hi) {
    if (value < static_cast<T>(lo)) return static_cast<T>(lo);
    if (value > static_cast<T>(hi)) return static_cast<T>(hi);
    return value;
}

// An ordinary exported function so the library has a real ABI surface.
int library_version();
