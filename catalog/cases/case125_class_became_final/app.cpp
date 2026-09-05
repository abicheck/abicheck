#ifdef USE_V2
#include "v2.h"
#else
#include "v1.h"
#endif
#include <cstdio>

// The break: a consumer that derives from Shape. This compiles against v1.h
// but FAILS to compile against v2.h ("cannot derive from 'final' class").
// The compiled binary linked against either .so is byte-identical at the ABI
// level — nothing in the object/symbol table records `final`.
struct MyShape : public Shape {
    int extra = 0;
};

int main() {
    MyShape m;
    printf("area=%f extra=%d\n", shape_area(&m), m.extra);
    return 0;
}
