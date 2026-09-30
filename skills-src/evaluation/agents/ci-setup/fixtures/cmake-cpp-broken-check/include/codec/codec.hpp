#pragma once
#include <cstddef>
namespace codec {
struct Options { int level; bool fast; };
std::size_t encode(const unsigned char *in, std::size_t n, unsigned char *out, const Options &opt);
}
