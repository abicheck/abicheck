#pragma once
#include <stddef.h>
#define DAAL_EXPORT
#include "algorithms/tree_utils/tree_utils_regression.h"
namespace tree = daal::algorithms::tree_utils;
using V = tree::regression::TreeNodeVisitor;
using S = tree::SplitNodeDescriptor;
using L = tree::regression::LeafNodeDescriptor;
bool visit(V&, const S&, const L&);
bool feed(V&);
