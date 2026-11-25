#include "daal.h"
using namespace daal;
using namespace daal::algorithms;
using namespace daal::data_management;
void ada(adaboost::Model& model, const classifier::ModelPtr& value) {
    model.addWeakLearnerModel(value);
}
void brown(brownboost::Model& model, const classifier::ModelPtr& value) {
    model.addWeakLearnerModel(value);
}
void logit(logitboost::Model& model, const regression::ModelPtr& value) {
    model.addWeakLearnerModel(value);
}
services::Status merged(MergedNumericTable& model, const NumericTablePtr& value) {
    return model.addNumericTable(value);
}
services::Status row(RowMergedNumericTable& model, const NumericTablePtr& value) {
    return model.addNumericTable(value);
}
auto soa(const NumericTableDictionaryPtr& dictionary) {
    return SOANumericTable::create(dictionary, 17);
}

#ifdef ONEDAL_ARGUMENT_CLIENT
extern "C" void* argument_token();

template <class P>
P argument() {
    return P(P(), static_cast<typename P::ElementType*>(argument_token()));
}

extern "C" int run_argument(int id) {
    switch (id) {
    case 0: { adaboost::Model m; ada(m, argument<classifier::ModelPtr>()); break; }
    case 1: { brownboost::Model m; brown(m, argument<classifier::ModelPtr>()); break; }
    case 2: { logitboost::Model m; logit(m, argument<regression::ModelPtr>()); break; }
    case 3: { MergedNumericTable m; merged(m, argument<NumericTablePtr>()); break; }
    case 4: { RowMergedNumericTable m; row(m, argument<NumericTablePtr>()); break; }
    case 5: soa(argument<NumericTableDictionaryPtr>()); break;
    }
    return 64; // The observation adapter exits at the SDK boundary.
}
#endif
