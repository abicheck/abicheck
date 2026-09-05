/* case37 v2: Base class changes applied */
#ifndef V2_HPP
#define V2_HPP

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

/* Scenario 1: base order SWAPPED (Serializer now first) */
class ReorderDemo : public Serializer, public Logger {
public:
    void process();
};

/* Scenario 2: base became VIRTUAL */
class VirtualDemo : public virtual Logger {
public:
    void init();
};

/* Scenario 3: new base ADDED */
class AddBaseDemo : public Logger, public Serializer {
public:
    void run();
};

#endif
