#ifndef CASE186_H
#define CASE186_H
#include <stddef.h>

typedef struct {
    char *data;
    size_t size;
} Buffer;

void send_buffer(char *data);

#endif
