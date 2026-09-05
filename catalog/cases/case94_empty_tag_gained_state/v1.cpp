#include "v1.h"

namespace mylib {

runner::runner() {}
int runner::run(int n, auto_partitioner) { return n * 2; }

extern "C" runner* mylib_make_runner() { return new runner(); }
extern "C" void mylib_free_runner(runner* p) { delete p; }

} // namespace mylib
