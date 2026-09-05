// v1: UTF-8 helpers using plain `char` (pre-C++20 / char8_t disabled).
// Self-contained (no header) so the snapshot uses the library's DWARF.
#include <cstddef>
#include <cstring>

struct Utf8View {
    const char *data;
    std::size_t size;
};

std::size_t utf8_length(const char* text) { return std::strlen(text); }

Utf8View utf8_make(const char* text, std::size_t n) {
    return Utf8View{text, n};
}
