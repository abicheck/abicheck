// v1: libcore exports four template instantiations under explicit names.
// In real oneDAL these would be mangled C++ symbols for
// train_ops<Float, Method, Task>; we use plain C names here so the
// example is reproducible without a specific demangler.
extern "C" int train_float_dense()    { return 1; }
extern "C" int train_float_sparse()   { return 2; }
extern "C" int train_double_dense()   { return 3; }
extern "C" int train_double_sparse()  { return 4; }
