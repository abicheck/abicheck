#pragma once

// v2: a new virtual function `rotate()` is inserted *between* `area()` and
// `perimeter()`. Every later vtable slot shifts down by one: `perimeter()` now
// lives where `area()`'s caller expects... nothing — and a v1 binary calling
// `perimeter()` through its fixed slot index dispatches to `rotate()` instead.
// The `_ZTV5Shape` vtable object grows by exactly one pointer, which abicheck
// can see from the ELF symbol size *without any debug info or headers*.
struct Shape {
    virtual int area();
    virtual int rotate();      // <-- inserted in the middle of the slot order
    virtual int perimeter();
    virtual ~Shape();
};

extern "C" Shape* make_shape();
