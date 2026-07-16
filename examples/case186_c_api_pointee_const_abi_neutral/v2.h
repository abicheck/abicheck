#ifndef CASE186_H
#define CASE186_H
#include <stddef.h>

/* data's pointee gained `const`: the field is still one pointer-width, at
 * the same offset -- only the mutability contract tightened (the library
 * now promises not to write through it).
 */
typedef struct {
    const char *data;
    size_t size;
} Buffer;

void send_buffer(const char *data);

#endif
