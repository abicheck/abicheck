"""Generate a typed facade over an exported byte-buffer operation.

The wire contract carries an element count and a type discriminator. A narrow
type can mask a unit mismatch, so the frozen client exercises two widths.
"""

from pathlib import Path

HEADER = """#include <cstddef>
#include <cstdint>
enum class Width : unsigned { byte = 1, word = 4 };
extern "C" void transfer(void*, const void*, std::size_t, Width);
template<class T> void copy_items(T* out, const T* in, std::size_t count, Width width) {
    transfer(out, in, count, width);
}
"""
CLIENT = """#include "api.hpp"
#include <array>
template<class T> bool check(Width width) {
    std::array<T, 3> input{17, 29, 41}, output{0, 0, 0};
    copy_items(output.data(), input.data(), 2, width);
    return output[0] == 17 && output[1] == 29 && output[2] == 0;
}
int main() {
    return (check<std::uint8_t>(Width::byte) ? 0 : 1)
         + (check<std::uint32_t>(Width::word) ? 0 : 2);
}
"""
BYTE_COUNTS = {
    "baseline": "count * static_cast<unsigned>(width)",
    "equivalent": "width == Width::byte ? count : 4 * count",
    "count_as_bytes": "count",
}


def write_sources(root: Path):
    (root / "api.hpp").write_text(HEADER)
    (root / "client.cpp").write_text(CLIENT)
    for name, expression in BYTE_COUNTS.items():
        (root / f"{name}.cpp").write_text(
            '#include "api.hpp"\n#include <cstring>\n'
            "void transfer(void* out, const void* in, std::size_t count, Width width) {\n"
            f"    std::memcpy(out, in, {expression});\n}}\n"
        )
