/* good.c — v2: CacheBlock aligned to 64 bytes for cache-line optimization. */
#include "good.h"
#include <string.h>

void block_init(CacheBlock *b) {
    memset(b->data, 0, sizeof(b->data));
    b->checksum = 0;
}

long block_checksum(const CacheBlock *b) {
    long sum = 0;
    for (int i = 0; i < (int)sizeof(b->data); i++)
        sum += b->data[i];
    return sum;
}

int block_process(CacheBlock *blocks, int count) {
    int ok = 0;
    for (int i = 0; i < count; i++) {
        if (block_checksum(&blocks[i]) == blocks[i].checksum)
            ok++;
    }
    return ok;
}
