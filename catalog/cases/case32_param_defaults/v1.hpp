/* case32: Parameter default value changes
 *
 * Three scenarios grouped in one example:
 * 1. Default value changed: timeout 30 → 60 (informational)
 * 2. Default value removed: verbose had default, now requires explicit arg (SOURCE_BREAK)
 * 3. Default value added: new param gets a default (compatible)
 *
 * abicheck detects: PARAM_DEFAULT_VALUE_CHANGED, PARAM_DEFAULT_VALUE_REMOVED
 * ABICC equivalent: Parameter_Default_Value_Changed, _Removed
 */
#ifndef V1_HPP
#define V1_HPP

class Connection {
public:
    void connect(int timeout = 30);
    void configure(bool verbose = true, int retries = 3);
    void disconnect(int code);  // no default in v1, will get one in v2
};

#endif
