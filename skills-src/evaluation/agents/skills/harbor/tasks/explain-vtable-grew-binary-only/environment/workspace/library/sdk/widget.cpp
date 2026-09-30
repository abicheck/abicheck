#include <cstdio>
#include "widget.h"
namespace widget {
Shape::~Shape() {}
void describe(const Shape &s) { std::printf("area=%d\n", s.area()); }
}
