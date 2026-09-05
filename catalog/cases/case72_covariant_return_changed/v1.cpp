#include "v1.h"

Shape::~Shape() {}
Shape *Shape::clone() const { return nullptr; }

Circle::Circle(int r) : radius_(r) {}

Circle *Circle::clone() const {
    return new Circle(radius_);
}

int Circle::area() const { return 3 * radius_ * radius_; }
int Circle::radius() const { return radius_; }
