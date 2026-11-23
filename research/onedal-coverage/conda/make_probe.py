"""Generate an in-process training/traversal client from the upstream example."""
from pathlib import Path
import sys
src=Path(sys.argv[1])/'examples/daal/cpp/source/decision_tree/dt_reg_traverse_model.cpp'
s=src.read_text().split('/** Visitor class')[0]
s=s.replace('    algorithm.compute();','    if (!algorithm.compute()) std::exit(2);')
s += """
using namespace daal::algorithms::tree_utils::regression;
class Probe : public TreeNodeVisitor {
public:
    bool first = true;
    bool onLeafNode(const LeafNodeDescriptor& d) override {
        if (first) { std::cout << "unexpected first leaf" << std::endl; std::exit(7); }
        std::cout << "L " << d.level << " " << d.response << " " << d.nNodeSampleCount << std::endl;
        return true;
    }
    bool onSplitNode(const SplitNodeDescriptor& d) override {
        first = false;
        std::cout << "S " << d.level << " " << d.featureIndex << " " << d.featureValue << " " << d.nNodeSampleCount << std::endl;
        return true;
    }
};
void printModel(const daal::algorithms::decision_tree::regression::Model& m) {
    Probe visitor;
    m.traverseDFS(visitor);
}
"""
Path('probe.cpp').write_text(s)
