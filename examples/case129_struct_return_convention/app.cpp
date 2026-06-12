// Consumer compiled against v1's in-register return convention. Self-contained
// (the struct/decl mirror v1) so the case needs no public header — the break is
// a DWARF/L1 fact, not a header-visible one.
struct Result {
    int code;
    double value;
};
Result compute();

int main() {
    Result r = compute();
    return r.code;
}
