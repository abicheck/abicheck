// Linked first in the "relink" control: unrelated code and vtables placed
// before the provider's, so every table of D moves to a new address.
struct Pad { virtual int f(); virtual int g(); long a[4]; };
int Pad::f() { return 1; }
int Pad::g() { return 2; }
extern "C" const Pad* pad_table() { static const Pad p{}; return &p; }
extern "C" void* const pad_ptrs[64] = {(void*)&pad_table};
