#include "v1.hpp"

int Base::paint(int x) { return x + 1; }
void Derived::helper() {}
Base* make_derived() { return new Derived(); }
