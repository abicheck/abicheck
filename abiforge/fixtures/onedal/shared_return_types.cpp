// SPDX-License-Identifier: Apache-2.0
#include "daal.h"
#include <cstdlib>
#include <cstring>
using daal::services::SharedPtr;

#ifdef PROVIDER
extern "C" void record_release(const void*);
static SharedPtr<int> owner;
extern "C" void release_owner() { owner.reset(); }

template<class P>
P make_return(int mode) {
    owner = SharedPtr<int>(new int(7), &record_release);
    // Neither side dereferences the alias. The deleter receives the original token.
    auto alias = static_cast<decltype(P().get())>(static_cast<void*>(owner.get()));
    P value(owner, alias, alias);
    if (mode == 1) return P(P(), alias);
    if (mode == 2) {
        static P extra;
        extra = value;
    }
    return value;
}
#define PROBE(ID, ...) extern "C" __VA_ARGS__ probe_##ID(int mode) { return make_return<__VA_ARGS__>(mode); }
#include "return_types.inc"
#else
static int releases;
extern "C" void record_release(const void* address) {
    ++releases;
    delete static_cast<const int*>(address);
}
extern "C" void release_owner();
#define PROBE(ID, ...) extern "C" __VA_ARGS__ probe_##ID(int);
#include "return_types.inc"
#undef PROBE
int main(int argc, char** argv) {
    if (argc != 3) return 64;
    const int mode = std::atoi(argv[2]);
#define PROBE(ID, ...) if (std::strcmp(argv[1], #ID) == 0) { \
        auto value = probe_##ID(mode); release_owner(); \
        if (releases) return 11; value.reset(); return releases == 1 ? 0 : 12; }
#include "return_types.inc"
    return 64;
}
#endif
