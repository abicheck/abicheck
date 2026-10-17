#include "v2.h"
namespace ns {
// Stops calling hdr_inline and stops instantiating P<float>, so both WEAK
// exports leave the .dynsym. weak_undeclared is not defined here at all.
int Pub::g() { return 2; }
detail::P<float>* Pub::q() { return nullptr; }
void* Pub::alloc() { return ::operator new(4); }
int strong_fn() { return 3; }
}
