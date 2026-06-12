// A small, trivially-copyable aggregate returned by value. By the System V
// AMD64 ABI a struct this small and trivial is returned in registers.
struct Result {
    int code;
    double value;
};

// Public factory: returns Result by value.
Result compute() { return Result{0, 1.5}; }
