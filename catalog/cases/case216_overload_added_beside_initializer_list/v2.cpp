#include "v2.h"

int mylib::pick(int) { return 1; }
int mylib::pick(std::initializer_list<int>) { return 2; }
int mylib::pick(double) { return 3; }
