/* v1 and v2 share byte-identical source. Only the compiler's
 * -fcf-protection flag differs between the two builds. */
int add(int a, int b) { return a + b; }
int sub(int a, int b) { return a - b; }

typedef int (*binop_fn)(int, int);

/* An indirect call through a function pointer -- exactly what CET's
 * Indirect Branch Tracking (IBT) is designed to protect: it requires the
 * call target to begin with an ENDBR64 instruction, so a corrupted function
 * pointer (e.g. via a buffer overflow) cannot be redirected into arbitrary
 * code as a jump-oriented-programming gadget. */
int dispatch(int which, int a, int b) {
    binop_fn table[2] = { add, sub };
    return table[which & 1](a, b);
}
