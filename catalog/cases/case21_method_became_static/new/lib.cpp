#include "lib.h"
#include <cstdio>

int Widget::bar() {
    std::printf("bar() called (static method), returning fixed value\n");
    return 7;
}
