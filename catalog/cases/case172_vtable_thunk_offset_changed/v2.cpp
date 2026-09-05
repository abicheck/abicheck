#include "v2.h"

int Base1::f1() { return 1; }
Base1::~Base1() {}

int Base2::f2() { return 2; }
Base2::~Base2() {}

int Derived::f2() { return 20; }

extern "C" Derived* make_derived() { return new Derived(); }
