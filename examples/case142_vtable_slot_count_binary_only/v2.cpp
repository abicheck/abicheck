#include "v2.h"

int Shape::area()      { return 10; }
int Shape::rotate()    { return 99; }
int Shape::perimeter() { return 20; }
Shape::~Shape()        {}

extern "C" Shape* make_shape() { return new Shape(); }
