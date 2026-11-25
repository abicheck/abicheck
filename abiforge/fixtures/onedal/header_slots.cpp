// SPDX-License-Identifier: Apache-2.0
#include "daal.h"
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <cstdlib>

// SysV AMD64 Itanium pointer-to-virtual-member representation.
// The first word is one plus the byte offset from the vtable address point.
template<class Member>
void slot(const char* method, Member member) {
    std::ptrdiff_t words[2];
    static_assert(sizeof(words) == sizeof(member));
    std::memcpy(words, &member, sizeof(words));
    if (!(words[0] & 1) || words[1] != 0) std::abort();
    std::printf("%s,%td\n", method, words[0]-1);
}
int main() {
    namespace pca = daal::algorithms::pca;
    slot("_ZNK4daal10algorithms3pca14quality_metric18explained_variance10interface19Parameter5checkEv", &pca::quality_metric::explained_variance::Parameter::check);
    slot("_ZNK4daal10algorithms3pca18quality_metric_set10interface19Parameter5checkEv", &pca::quality_metric_set::Parameter::check);
    slot("_ZNK4daal15data_management10interface119NumericTableFeature19getSerializationTagEv", &daal::data_management::NumericTableFeature::getSerializationTag);
}
