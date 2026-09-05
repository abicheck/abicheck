// v2 gives Result a user-declared destructor. That makes it non-trivial, so the
// ABI now returns it via a hidden caller-provided pointer (sret) instead of in
// registers — the aggregate-return convention flipped for compute(), even though
// the mangled name (_Z7computev) is unchanged.
struct Result {
    int code;
    double value;
    ~Result();
};

Result::~Result() {}
Result compute() { return Result{0, 1.5}; }
