/* case38 v2: Virtual + deleted changes applied */
#ifndef V2_HPP
#define V2_HPP

class Processor {
public:
    /* Scenario 1: became virtual */
    virtual void transform(int data);

    /* Scenario 2: lost virtual */
    void validate(int data);

    /* Scenario 3: became pure virtual */
    virtual void execute() = 0;

    /* Scenario 4: explicitly deleted */
    Processor(const Processor &other) = delete;

    Processor() = default;
    virtual ~Processor() = default;
};

#endif
