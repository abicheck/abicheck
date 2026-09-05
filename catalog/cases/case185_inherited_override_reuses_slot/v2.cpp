#include "v2.hpp"

int Base::paint(int x) { return x + 1; }
int Derived::paint(int x) { return x + 2; }
void Derived::helper() {}
Base* make_derived() { return new Derived(); }
