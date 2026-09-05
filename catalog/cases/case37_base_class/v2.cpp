#include "v2.hpp"
void Logger::log(const char *msg) { (void)msg; ++log_level; }
void Serializer::serialize(const char *data) { (void)data; ++format; }
void ReorderDemo::process() { log_level = 10; format = 20; }
void VirtualDemo::init() { log_level = 77; }
void AddBaseDemo::run() { log_level = 33; format = 44; }
