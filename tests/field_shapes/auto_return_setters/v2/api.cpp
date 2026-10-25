#include "api.h"
static int g;
auto& S::set_x() { return *this; }
auto S::get() { return x; }
auto& ref() { return g; }
auto val(int a) { return a; }
