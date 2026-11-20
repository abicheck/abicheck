#include <dlfcn.h>
#include <stdio.h>
int main(void) {
    for (int i = 0; i < 2; ++i) {
        void *h = dlopen("./life.so", RTLD_NOW | RTLD_LOCAL);
        if (!h) return 1;
        int (*next)(void) = dlsym(h, "next");
        if (!next) return 2;
        printf("%d\n", next());
        if (dlclose(h)) return 3;
    }
}
