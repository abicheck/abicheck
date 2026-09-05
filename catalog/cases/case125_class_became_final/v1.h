#pragma once

// A public, extensible base class. Consumers are expected to derive from it
// (e.g. `class MyShape : public Shape { ... }`).
class Shape {
public:
    Shape();
    double area() const;

private:
    double scale_;
};

// Public API that puts `Shape` on the exported ABI surface.
Shape *make_shape(double scale);
double shape_area(const Shape *s);
