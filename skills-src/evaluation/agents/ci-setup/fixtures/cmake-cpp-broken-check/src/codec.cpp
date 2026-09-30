#include "codec/codec.hpp"
#include <cstring>
namespace codec {
std::size_t encode(const unsigned char *in, std::size_t n, unsigned char *out, const Options &) { std::memcpy(out, in, n); return n; }
}
