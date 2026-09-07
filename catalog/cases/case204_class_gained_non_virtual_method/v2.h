#ifndef CASE204_H
#define CASE204_H

/* A new *non-virtual* member function. It adds an exported symbol and
 * nothing else: no vptr, so sizeof(Handle), its alignment and every member
 * offset are unchanged.
 */
class Handle {
public:
    Handle();
    int  id() const;
    void close();
    bool is_open() const;
private:
    int id_;
};

Handle *make_handle();

#endif
