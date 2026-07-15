#include <stdio.h>

/* App compiled against v1: aligned(8), sizeof=64 */
typedef struct __attribute__((aligned(8))) {
    char data[56];
    long checksum;
} CacheBlock;

extern void block_init(CacheBlock *b);
extern long block_checksum(const CacheBlock *b);
extern int block_process(CacheBlock *blocks, int count);

int main(void) {
    CacheBlock blocks[4];
    for (int i = 0; i < 4; i++) {
        block_init(&blocks[i]);
        blocks[i].data[0] = (char)(i + 1);
        /* block_checksum() sums the mutated data[]; store it back so the
         * per-block equality check in block_process() has something correct
         * to compare against (block_init() only zeroed the checksum). */
        blocks[i].checksum = block_checksum(&blocks[i]);
    }

    int ok = block_process(blocks, 4);
    printf("block_process = %d (expected 4)\n", ok);

    if (ok != 4) {
        printf("WRONG RESULT: alignment change caused array stride/layout mismatch\n");
        return 1;
    }

    /* Alignment-only breaks may not show at runtime on x86_64 (forgiving alignment),
     * but can cause crashes on strict-alignment architectures (ARM/RISC-V)
     * and are undefined behavior per C/C++ standards.
     */
    printf("OK on this arch (BREAKING in strict sense: alignment ABI changed)\n");
    return 0;
}