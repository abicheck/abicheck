#include "v1.hpp"
#include <cstdio>
/* Scenario A: derived class provides execute() — works with both v1 and v2 */
class MyProcessor : public Processor {
public:
    void execute() override {
        std::printf("MyProcessor::execute() called\n");
    }
};

/* Scenario B: instantiate base Processor directly.
 * IMPORTANT: this file MUST be compiled against v1.hpp — in v1, Processor is concrete.
 * With libv2.so swapped in at runtime: vtable[execute] = __cxa_pure_virtual -> SIGABRT.
 * (If compiled against v2.hpp, the compiler rejects this: abstract class instantiation.)
 */
int main() {
    std::printf("=== Scenario A: derived class (should always work) ===\n");
    MyProcessor proc;
    Processor& ref = proc;
    ref.transform(42);
    ref.validate(10);
    ref.execute();

    std::printf("\n=== Scenario B: base class instantiated directly (ABI break!) ===\n");
    /* App compiled with v1.hpp (execute is non-pure virtual, Processor is concrete).
     * At runtime with libv2.so swapped in, vtable[execute] = __cxa_pure_virtual -> abort(). */
    Processor* base = new Processor();
    /* With v2: vtable[execute] = __cxa_pure_virtual -> std::abort() -> SIGABRT (signal 6, exit 134).
     * Exit 134 != 2 but counts as "detected break": any non-zero exit = runtime incompatibility.
     * Note: delete base is unreachable on the v2 path (intentional — process aborts). */
    base->execute();
    delete base;  /* reached only with v1; with v2 abort() fires first */

    return 0;
}
