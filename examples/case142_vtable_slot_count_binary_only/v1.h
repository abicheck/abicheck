#pragma once

// v1: a polymorphic class with two virtual functions (plus a virtual
// destructor). Its vtable slot order is fixed: [area, perimeter, ~Shape...].
struct Shape {
    virtual int area();
    virtual int perimeter();
    virtual ~Shape();
};

extern "C" Shape* make_shape();
