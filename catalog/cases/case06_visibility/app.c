#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

static int check_visibility(const char *path, const char *label,
                            int expect_exported, int fail_on_hidden) {
    void *handle = dlopen(path, RTLD_NOW);
    if (!handle) {
        fprintf(stderr, "dlopen %s: %s\n", path, dlerror());
        return 1;
    }

    void *sym = dlsym(handle, "internal_helper");
    printf("%s: internal_helper %s\n", label, sym ? "EXPORTED" : "hidden");

    int failure = 0;
    if (expect_exported && !sym) {
        printf("WRONG RESULT: %s no longer exports internal_helper\n", label);
        failure = 1;
    } else if (!expect_exported && fail_on_hidden && !sym) {
        printf("WRONG RESULT: %s hides internal_helper (symbol removed)\n", label);
        failure = 1;
    }

    dlclose(handle);
    return failure;
}

int main(void) {
    int failed = 0;
    failed |= check_visibility("./libv1.so", "libv1.so (bad)", 1, 0);
    failed |= check_visibility("./libv2.so", "libv2.so (good)", 0, 1);
    return failed ? EXIT_FAILURE : EXIT_SUCCESS;
}
