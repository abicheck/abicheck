#pragma once

namespace audio {

// v2 raises the constant to 16. Consumers compiled against v1 still have 8
// baked in; mixing the two (old consumer + new library, or TUs built against
// different header versions) overflows buffers / disagrees on sizes. The
// library binary is byte-identical (no symbol for a constexpr), so this is
// invisible to object/DWARF comparison — only header analysis sees it.
constexpr int kMaxChannels = 16;

int mix(const float *in, int n);

}  // namespace audio
