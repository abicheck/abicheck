#include "probe.hpp"
#include <cstdio>
struct Client : V {
    int step = 0;
    bool onSplitNode(const S& s) override {
        if (++step != 1) return false;
        return s.level == 3 && s.impurity == 1.5 && s.nNodeSampleCount == 42 &&
               s.featureIndex == 7 && s.featureValue == 4.5 && s.coverValue == 8.5;
    }
    bool onLeafNode(const L& l) override {
        if (++step != 2) return false;
        return l.response == 12.25 && l.coverValue == 17.25;
    }
};
int main() {
    S s{}; L l{}; Client c;
    return feed(c) ? 0 : 1;
}
