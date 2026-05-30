#include <string>
#include <vector>

// Declarations mirror the library surface (kept local so the case needs no
// public header — see v1.cpp).
std::string join(const std::string& a, const std::string& b);
std::string repeat(const std::string& s, int n);

int main() {
    std::string s = join("a", "b");
    return repeat(s, 2).empty() ? 1 : 0;
}
