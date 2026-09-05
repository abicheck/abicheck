#pragma once

// A function template shipped header-only. The library instantiates nothing,
// so NO symbol is emitted for it — consumers instantiate it themselves.
template <typename T>
T clamp(T value, int lo, int hi) {
    if (value < static_cast<T>(lo)) return static_cast<T>(lo);
    if (value > static_cast<T>(hi)) return static_cast<T>(hi);
    return value;
}

// An ordinary exported function so the library has a real ABI surface.
int library_version();
