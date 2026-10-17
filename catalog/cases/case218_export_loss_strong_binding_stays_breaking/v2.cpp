#include "v2.h"
namespace ns {
// Stops calling hdr_inline and stops instantiating P<float>, so both WEAK
// exports leave the .dynsym, as in case217. weak_undeclared is not defined
// here at all.
int Pub::g() { return 2; }
detail::P<float>* Pub::q() { return nullptr; }
void* Pub::alloc() { return ::operator new(4); }
// strong_fn stays declared in v2.h but is not defined here: a GLOBAL export
// removal the binding rule must not demote.
}
