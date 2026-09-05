#include "v1.h"
#include <cstdio>

extern "C" Widget* make_widget();

int main() {
    Widget* w = make_widget();
    int draw = w->draw();
    int resize = w->resize();
    std::printf("draw()   = %d (expected 10)\n", draw);
    std::printf("resize() = %d (expected 20)\n", resize);
    delete w;

    if (draw != 10 || resize != 20) {
        std::printf("WRONG RESULT: vtable layout changed\n");
        return 1;
    }
    return 0;
}
