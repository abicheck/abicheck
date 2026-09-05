// v2: UTF-8 helpers migrated to char8_t (C++20). char8_t is a distinct type,
// so the struct field type and the function signatures (and mangled names)
// change. Self-contained (no header).
#include <cstddef>
#include <string>

struct Utf8View {
    const char8_t *data;
    std::size_t size;
};

std::size_t utf8_length(const char8_t* text) {
    return std::char_traits<char8_t>::length(text);
}

Utf8View utf8_make(const char8_t* text, std::size_t n) {
    return Utf8View{text, n};
}
