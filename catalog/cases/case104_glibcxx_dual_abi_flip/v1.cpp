// Self-contained on purpose: the libstdc++ dual-ABI flip is a binary/symbol
// phenomenon, so this case is validated from the built shared objects (no
// public header is handed to the snapshotter). v1 and v2 are identical source;
// only the _GLIBCXX_USE_CXX11_ABI compile flag differs (see CMakeLists.txt).
#include <string>
#include <vector>

std::string join(const std::string& a, const std::string& b) { return a + b; }
std::string upper(const std::string& s) { return s; }
std::string repeat(const std::string& s, int n) {
    std::string r;
    for (int i = 0; i < n; ++i) r += s;
    return r;
}
std::string trim(const std::string& s) { return s; }
std::vector<std::string> split(const std::string& s, char) { return {s}; }
std::string concat(const std::vector<std::string>& parts) {
    std::string r;
    for (const auto& p : parts) r += p;
    return r;
}
