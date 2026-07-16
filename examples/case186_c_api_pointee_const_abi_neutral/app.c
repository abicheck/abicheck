#include "v1.h"
#include <stdio.h>

int main(void) {
    char msg[] = "hello";
    Buffer b;
    b.data = msg;
    b.size = sizeof(msg) - 1;
    send_buffer(b.data);
    printf("sent %zu bytes\n", b.size);
    return 0;
}
