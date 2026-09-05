/* bad.h — CacheBlock aligned to 8 bytes (default for most structs on LP64). */
#ifndef MYLIB_H
#define MYLIB_H

typedef struct __attribute__((aligned(8))) {
    char data[56];
    long checksum;
} CacheBlock;

void block_init(CacheBlock *b);
long block_checksum(const CacheBlock *b);
int block_process(CacheBlock *blocks, int count);

#endif
