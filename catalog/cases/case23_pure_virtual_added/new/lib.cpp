#include "lib.h"
#include <cstdio>
#include <cstdlib>

/* A pure virtual function may still have an out-of-line definition — it's
   only reachable via an explicit qualified call (Processor::process()),
   never through the vtable of the abstract class itself, so this changes
   no runtime behavior at all. It's kept here so v2's binary/DWARF still
   contains a Processor::process() symbol to compare against v1's, letting
   abicheck's pure-virtual-status-change detector see this as the *same*
   member function transitioning from concrete to pure virtual (calibrating
   FUNC_PURE_VIRTUAL_ADDED) rather than as a plain removal. Without this
   body, v2 has no Processor::process() symbol at all — DWARF/mangled-name
   comparison then has no v2-side counterpart to pair with v1's, and the
   change is (still correctly, but less precisely) reported as a removal. */
void Processor::process() { std::fprintf(stderr, "process() default (unused)\n"); }

/* Concrete subclass — needed because Processor is abstract in v2 (process()=0),
   so we cannot directly instantiate it. ProcAbortImpl::process() calls abort()
   to simulate the real failure: in a true mixed-version scenario, an old binary
   that directly calls 'new Processor()' (v1 concrete) and then p->process() would
   hit the __cxa_pure_virtual handler in the v2 vtable and abort. */
struct ProcAbortImpl : Processor {
    void process() override {
        std::fprintf(stderr, "pure virtual method called\n");
        std::abort();
    }
};

extern "C" Processor* make_proc() { return new ProcAbortImpl(); }
