#include "v1.h"
#include <math.h>
#include <stdio.h>

int main(void) {
    /* Compiled against v1: Packet.data[] is float, packet_sum should be 10.0 */
    struct Packet *p = packet_create(1, 4);
    float sum = packet_sum(p);
    printf("packet_sum = %.1f\n", sum);
    printf("Expected: 10.0\n");
    packet_free(p);

    if (fabsf(sum - 10.0f) > 0.01f) {
        printf("WRONG RESULT: flexible-array element type changed\n");
        return 1;
    }
    return 0;
}
