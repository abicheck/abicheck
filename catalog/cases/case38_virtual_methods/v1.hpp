/* case38: Virtual method changes + deleted functions (4 scenarios)
 *
 * 1. FUNC_VIRTUAL_ADDED: non-virtual → virtual
 *    Binary break: vtable added, object layout changes
 *
 * 2. FUNC_VIRTUAL_REMOVED: virtual → non-virtual
 *    Binary break: vtable slot removed, old binaries call wrong address
 *
 * 3. FUNC_VIRTUAL_BECAME_PURE: virtual → pure virtual (= 0)
 *    Old derived classes missing implementation → null call at runtime
 *
 * 4. FUNC_DELETED: function marked = delete
 *    Previously callable function now explicitly deleted
 *
 * abicheck detects: FUNC_VIRTUAL_ADDED, FUNC_VIRTUAL_REMOVED,
 *                   FUNC_VIRTUAL_BECAME_PURE, FUNC_DELETED
 */
#ifndef V1_HPP
#define V1_HPP

class Processor {
public:
    /* Scenario 1: will become virtual in v2 */
    void transform(int data);

    /* Scenario 2: virtual, will lose virtual in v2 */
    virtual void validate(int data);

    /* Scenario 3: virtual, will become pure virtual in v2 */
    virtual void execute();

    /* Scenario 4: will be = delete'd in v2 */
    Processor(const Processor &other);

    Processor() = default;
    virtual ~Processor() = default;
};

#endif
