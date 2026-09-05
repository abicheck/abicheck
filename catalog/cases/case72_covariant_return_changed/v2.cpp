#include "v2.h"

Shape::~Shape() {}
Shape *Shape::clone() const { return nullptr; }

Drawable::Drawable() : color_(0) {}
int Drawable::color() const { return color_; }

Circle::Circle(int r) : radius_(r) {}

Drawable *Circle::clone() const {
    return new Circle(radius_);
}

int Circle::area() const { return 3 * radius_ * radius_; }
int Circle::radius() const { return radius_; }
