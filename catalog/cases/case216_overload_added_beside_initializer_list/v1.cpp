#include "v1.h"

int mylib::pick(int) { return 1; }
int mylib::pick(std::initializer_list<int>) { return 2; }
