#include "v1.h"

Shape::Shape() : scale_(1.0) {}
double Shape::area() const { return scale_ * scale_; }

Shape *make_shape(double scale) {
    Shape *s = new Shape();
    (void)scale;
    return s;
}
double shape_area(const Shape *s) { return s->area(); }
