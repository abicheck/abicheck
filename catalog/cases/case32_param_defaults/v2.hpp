/* case32 v2: Default values changed/removed/added */
#ifndef V2_HPP
#define V2_HPP

class Connection {
public:
    void connect(int timeout = 60);         // changed: 30 → 60
    void configure(bool verbose, int retries = 5);  // verbose lost default, retries changed
    void disconnect(int code = 0);          // added default (compatible)
};

#endif
