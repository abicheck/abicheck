/* Illustrative only — dlopen() a plugin *after* the process has already
 * started, then call into it. Whether this succeeds or fails depends on how
 * much static-TLS "surplus" glibc reserved at startup (a host- and
 * glibc-version-dependent budget), so this demo is NOT the CI-graded proof —
 * see README.md. It is included to show what "may no longer be reliably
 * dlopen()able" means in practice. */
#include <dlfcn.h>
#include <stdio.h>

typedef int (*bump_fn)(void);

int main(void) {
    void *handle = dlopen("./libv1.so", RTLD_NOW | RTLD_NODELETE);
    if (!handle) {
        printf("dlopen FAILED: %s\n", dlerror());
        printf("(expected outcome when static-TLS surplus is exhausted)\n");
        return 1;
    }
    bump_fn bump = (bump_fn)dlsym(handle, "bump");
    if (!bump) {
        printf("dlsym FAILED: %s\n", dlerror());
        return 1;
    }
    printf("dlopen succeeded; bump() = %d\n", bump());
    dlclose(handle);
    return 0;
}
