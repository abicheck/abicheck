/* good.h — CacheBlock alignment increased to 64 bytes (cache-line aligned).
   Fields and sizes are identical; only the alignment requirement changes. */
#ifndef MYLIB_H
#define MYLIB_H

typedef struct __attribute__((aligned(64))) {
    char data[56];
    long checksum;
} CacheBlock;

void block_init(CacheBlock *b);
long block_checksum(const CacheBlock *b);
int block_process(CacheBlock *blocks, int count);

#endif
