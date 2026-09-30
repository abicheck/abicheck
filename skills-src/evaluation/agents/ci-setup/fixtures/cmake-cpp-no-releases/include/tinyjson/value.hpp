#pragma once
#include <string>
namespace tinyjson {
class Value {
public:
    Value();
    explicit Value(double number);
    bool is_number() const;
    double as_number() const;
    std::string dump() const;
private:
    int kind_;
    double number_;
};
Value parse(const std::string &text);
}
