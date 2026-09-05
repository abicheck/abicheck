#pragma once

namespace audio {

// A public compile-time constant. Consumers size buffers with it
// (`float buf[kMaxChannels]`), so the value 8 is BAKED INTO every consumer
// binary at compile time.
constexpr int kMaxChannels = 8;

int mix(const float *in, int n);

}  // namespace audio
