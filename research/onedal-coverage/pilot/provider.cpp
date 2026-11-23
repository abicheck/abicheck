#include "probe.hpp"
bool visit(V& visitor, const S& split, const L& leaf) {
    return visitor.onSplitNode(split) && visitor.onLeafNode(leaf);
}
bool feed(V& visitor) {
    S s{}; L l{};
    s.level = 3; s.impurity = 1.5; s.nNodeSampleCount = 42;
    s.featureIndex = 7; s.featureValue = 4.5; s.coverValue = 8.5;
    l.response = 12.25; l.coverValue = 17.25;
    return visit(visitor,s,l);
}
