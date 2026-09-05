/* case37: Base class changes (3 scenarios)
 *
 * 1. BASE_CLASS_POSITION_CHANGED: base class order swapped
 *    class D : A, B → class D : B, A  (this-ptr adjustments change)
 *
 * 2. BASE_CLASS_VIRTUAL_CHANGED: base became virtual
 *    class D : A → class D : virtual A  (layout changes for diamond)
 *
 * 3. TYPE_BASE_CHANGED: base class added/removed
 *    class D : A → class D : A, C  (new base added)
 *
 * All are BREAKING: object layout changes silently corrupt memory.
 *
 * abicheck detects: BASE_CLASS_POSITION_CHANGED, BASE_CLASS_VIRTUAL_CHANGED, TYPE_BASE_CHANGED
 * ABICC equivalent: Base_Class_Position, Base_Class_Became_Virtually_Inherited, Added_Base_Class
 */
#ifndef V1_HPP
#define V1_HPP

class Logger {
public:
    virtual void log(const char *msg);
    int log_level;
};

class Serializer {
public:
    virtual void serialize(const char *data);
    int format;
};

/* Scenario 1: base order will be swapped */
class ReorderDemo : public Logger, public Serializer {
public:
    void process();
};

/* Scenario 2: non-virtual base will become virtual */
class VirtualDemo : public Logger {
public:
    void init();
};

/* Scenario 3: new base class will be added */
class AddBaseDemo : public Logger {
public:
    void run();
};

#endif
