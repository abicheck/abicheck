/* case47 v2: add() moved out-of-line — symbol now exported */
#ifndef CASE47_V2_HPP
#define CASE47_V2_HPP

class Calculator {
public:
    int add(int a, int b);      /* no longer inline */
    int multiply(int a, int b);
    int subtract(int a, int b);
};

#endif
