// Consumer that depends on the v1 policy-tag names.
#include "v1.h"  // swap to v2.h to see the source break

int main() {
    // Old names — fail to compile against v2.h:
    mylib::flow::queueing  q_policy;
    (void)q_policy;
    mylib::flow::queue_node node;
    return node.run(0);
}
