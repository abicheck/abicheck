/* Reduced stand-in for libsvs_c_api's C header: the whole public contract. */
#pragma once
#ifdef __cplusplus
extern "C" {
#endif
__attribute__((visibility("default"))) int svs_index_build(const float* data, int n);
__attribute__((visibility("default"))) int svs_format(char* buf, int cap, unsigned value);
#ifdef __cplusplus
}
#endif
