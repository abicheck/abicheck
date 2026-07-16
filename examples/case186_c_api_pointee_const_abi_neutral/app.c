#include "v1.h"
#include <stdio.h>

int main(void) {
    char msg[] = "hello";
    send_buffer(msg);
    printf("sent %zu bytes\n", sizeof(msg) - 1);
    return 0;
}
