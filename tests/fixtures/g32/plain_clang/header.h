// Minimal public header used to capture a single-context `clang
// -ast-dump=json` document (see ../README.md, Fixture 1). Kept deliberately
// tiny — this fixture exists only to contrast against a multi-document AST
// stream, not to exercise any particular ABI feature.
struct Point {
    int x;
    int y;
};

int add(int a, int b);
