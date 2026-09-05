// app.cpp — compiled against the v1 header, where reset() is noexcept.
//
// The try/catch and the Sentinel are load-bearing, not decoration: without
// them this program would terminate on any uncaught exception, which proves
// nothing about `noexcept`. With them, the run distinguishes the two
// outcomes — either the handler catches and the destructor runs (an ordinary
// exception), or neither happens (the caller's frame was compiled on the
// no-throw promise and carries no cleanup landing pad for this call).
#include "v1.h"
#include <cstdio>
#include <stdexcept>

struct Sentinel {
    ~Sentinel() { std::printf("SENTINEL DESTRUCTOR RAN\n"); }
};

int main() {
    Buffer* b = make_buf();
    std::printf("Calling reset()...\n");
    try {
        Sentinel s;
        // b->reset() is declared noexcept in v1.h — the compiler omits the
        // cleanup landing pad for this call. With v2.so, reset() throws.
        b->reset();
        std::printf("reset() completed OK\n");
    } catch (const std::exception& e) {
        std::printf("CAUGHT: %s\n", e.what());
    }
    std::printf("main returning normally\n");
    free_buf(b);
    return 0;
}
