/* Standalone test for the deleted copy constructor scenario.
 *
 * Build against v1 (copy ctor exists), then swap in v2 .so
 * (copy ctor = delete) to see the undefined symbol error at load time.
 *
 * Usage:
 *   g++ -g copy_ctor_demo.cpp -I. -L. -lprocessor -Wl,-rpath,. -o copy_ctor_demo
 *   ./copy_ctor_demo          # works with v1
 *   # swap v2.so → undefined symbol error for Processor copy ctor
 */
#include "v1.hpp"
#include <cstdio>

class MyProcessor : public Processor {
public:
    void execute() override {
        std::printf("MyProcessor::execute() called\n");
    }
};

int main() {
    MyProcessor proc;

    std::printf("Copying processor...\n");
    Processor copy(proc);
    std::printf("Copy created successfully\n");

    return 0;
}
