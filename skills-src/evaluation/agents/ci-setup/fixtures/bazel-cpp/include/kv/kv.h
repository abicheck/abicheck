#pragma once
#include <string>
namespace kv {
class Store {
public:
    bool put(const std::string &k, const std::string &v);
    bool get(const std::string &k, std::string *v) const;
private:
    void *impl_ = nullptr;
};
}
