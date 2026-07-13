#include "v2.h"

int Base1::f1() { return 1; }
Base1::~Base1() {}

int Base2::helper() { return 2; }
Base2::~Base2() {}

extern "C" Derived* make_derived() { return new Derived(); }
extern "C" unsigned long derived_size() { return sizeof(Derived); }
