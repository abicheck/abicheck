#ifdef USE_V2
#include "v2.h"
#else
#include "v1.h"
#endif
#include <cstdio>

int main() {
    // The constant is baked into this binary at compile time. An app built
    // against v1 carries 8; rebuilt against v2 it carries 16 — a silent ABI
    // contract change between versions.
    float buf[audio::kMaxChannels] = {0};
    printf("channels=%d mixed=%d\n", audio::kMaxChannels, audio::mix(buf, audio::kMaxChannels));
    return 0;
}
