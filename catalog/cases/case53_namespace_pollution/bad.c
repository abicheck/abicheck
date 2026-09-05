/* bad.c — library uses generic symbol names without a library prefix.
   Names like init(), process(), cleanup() are extremely common and will
   collide with other libraries or application code. */

static int state = 0;

int init(void) {
    state = 1;
    return 0;
}

int process(int data) {
    if (!state) return -1;
    return data * 2 + 1;
}

void cleanup(void) {
    state = 0;
}

int status(void) {
    return state;
}
