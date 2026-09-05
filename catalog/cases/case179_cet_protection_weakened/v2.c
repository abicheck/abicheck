/* v2: identical logic to v1.c, built with -fcf-protection=none instead of
 * v1's -fcf-protection=full. The ENDBR64 landing pads and shadow-stack
 * instrumentation CET relies on are omitted from this build. */
int add(int a, int b) { return a + b; }
int sub(int a, int b) { return a - b; }

typedef int (*binop_fn)(int, int);

int dispatch(int which, int a, int b) {
    binop_fn table[2] = { add, sub };
    return table[which & 1](a, b);
}
