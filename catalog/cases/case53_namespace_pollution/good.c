/* good.c — same library with properly prefixed symbol names.
   All exported functions use the "mylib_" prefix to avoid collisions. */

static int state = 0;

int mylib_init(void) {
    state = 1;
    return 0;
}

int mylib_process(int data) {
    if (!state) return -1;
    return data * 2 + 1;
}

void mylib_cleanup(void) {
    state = 0;
}

int mylib_status(void) {
    return state;
}
