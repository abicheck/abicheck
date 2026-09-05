#include <stdio.h>

typedef struct {
    int version;
    int __reserved1;
    int __reserved2;
    int flags;
} Config;

extern Config* config_create(void);
extern void config_destroy(Config *c);
extern int config_get_flags(const Config *c);

int main(void) {
    Config *c = config_create();
    if (!c) {
        fprintf(stderr, "config_create failed\n");
        return 1;
    }
    printf("flags = %d\n", config_get_flags(c));
    config_destroy(c);
    return 0;
}
