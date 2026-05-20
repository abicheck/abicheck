// v2: train_double_sparse silently dropped from the instantiation list
// (perhaps a build-system regression). Per-library compare flags one
// func_removed but cannot distinguish "intentional internal symbol
// removal" from "promised public instantiation dropped".
extern "C" int train_float_dense()    { return 1; }
extern "C" int train_float_sparse()   { return 2; }
extern "C" int train_double_dense()   { return 3; }
// train_double_sparse removed.
