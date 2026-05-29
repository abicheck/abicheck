#include "v1.hpp"
#include <cstdio>

int main() {
    std::string s = join("a", "b");
    std::printf("%s\n", repeat(s, 2).c_str());
    return 0;
}
