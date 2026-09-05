// case115 — consumer that defines USE_FEATURE sees and calls
// lib::extended(). A consumer who does NOT define it cannot compile
// this file. The diverging public surface is the point.
#define USE_FEATURE 1
#include "v1.h"

int main() {
    lib::basic();
    lib::extended();
    return 0;
}
