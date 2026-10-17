#include "v1.h"
namespace ns {
int Pub::g() { return 2 + hdr_inline(); }
detail::P<float>* Pub::q() { return new detail::P<float>(); }
void* Pub::alloc() { return ::operator new(4); }
int strong_fn() { return 3; }
// WEAK export that no public header declares.
__attribute__((weak)) int weak_undeclared() { return 7; }
}
