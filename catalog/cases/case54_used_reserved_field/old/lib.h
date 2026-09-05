/* bad.h — struct with reserved fields (padding for future use). */
#ifndef MYLIB_H
#define MYLIB_H

typedef struct {
    int version;
    int __reserved1;
    int __reserved2;
    int flags;
} Config;

Config* config_create(void);
void config_destroy(Config *c);
int config_get_flags(const Config *c);

#endif
