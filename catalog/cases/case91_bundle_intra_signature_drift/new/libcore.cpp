// v2: core_add now takes/returns long. extern "C" => same mangled name as v1.
// libalgo's machine code still pushes int args / reads int return.
extern "C" long core_add(long a, long b) { return a + b; }
