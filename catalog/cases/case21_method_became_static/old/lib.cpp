#include "lib.h"
#include <cstdio>

int Widget::bar() {
    std::printf("bar() called (instance method), value=%d\n", value);
    return value + 1;
}
