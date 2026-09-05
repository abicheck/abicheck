#include <cstdio>

struct Base {
    virtual ~Base() = default;
    virtual int paint(int x);
};

extern Base* make_derived();

int main() {
    Base* b = make_derived();
    std::printf("paint(3) -> %d\n", b->paint(3));
    delete b;
    return 0;
}
