#pragma once

// v2 marks the class `final`. Layout, mangled names and vtable are unchanged,
// so already-compiled binaries keep running — but any consumer that derived
// from `Shape` no longer compiles. A pure source/API break.
class Shape final {
public:
    Shape();
    double area() const;

private:
    double scale_;
};

// Public API that puts `Shape` on the exported ABI surface.
Shape *make_shape(double scale);
double shape_area(const Shape *s);
