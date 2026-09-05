#include "v1.h"
#include <cstring>
Buffer::Buffer() { std::memset(data, 0, sizeof(data)); }
int Buffer::size() { return 64; }
extern "C" Buffer* make_buffer() { return new Buffer(); }
