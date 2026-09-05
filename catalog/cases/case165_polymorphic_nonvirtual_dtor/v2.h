#ifndef RENDER_H
#define RENDER_H

/* Unchanged from v1 — safe polymorphic type. */
class Renderer {
public:
    Renderer();
    virtual ~Renderer();
    virtual void draw(int frame);
    int frames_drawn;
};

Renderer* make_renderer();

/* NEW in v2: a polymorphic type (write() gives it a vtable) whose
   destructor is NOT virtual. The factory below hands out owning
   pointers, so callers will `delete` through this type — and any
   future subclass returned by the factory is destroyed through the
   base: undefined behavior, derived destructors silently skipped. */
class Exporter {
public:
    Exporter();
    ~Exporter(); /* NOT virtual — the anti-pattern */
    virtual void write(const char* path);
    long bytes_written;
};

/* Factory: the caller owns the returned object and deletes it. */
Exporter* make_exporter();

#endif
