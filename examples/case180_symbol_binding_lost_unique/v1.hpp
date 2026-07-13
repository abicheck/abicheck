#pragma once

// A classic Meyers-singleton registry: a function-local static inside a
// class template. v1 and v2 build IDENTICAL source -- only the
// -f(no-)gnu-unique compiler flag differs between the two builds.
template <typename T>
struct Registry {
    static T& instance() {
        static T value{};
        return value;
    }
};

struct Widget {
    int id = 42;
};

extern "C" int get_widget_id();
