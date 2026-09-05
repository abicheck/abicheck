/* case34: Access level changes (public → private/protected)
 *
 * Source-level break: external code can no longer access the member.
 * Binary layout is unchanged (access specifiers are compile-time only).
 *
 * Two scenarios:
 * 1. Method became private: Widget::helper() public → private
 * 2. Field became private: Widget::cache public → private
 *
 * abicheck detects: METHOD_ACCESS_CHANGED, FIELD_ACCESS_CHANGED
 * ABICC equivalent: Method_Became_Private, Field_Became_Private
 */
#ifndef V1_HPP
#define V1_HPP

class Widget {
public:
    void render();
    void helper();          // will become private in v2
    int cache;              // will become private in v2

protected:
    void internal_init();   // will become public in v2
};

#endif
