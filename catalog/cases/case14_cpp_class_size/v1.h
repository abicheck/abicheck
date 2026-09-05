class Buffer {
public:
    Buffer();
    int size();
private:
    char data[64];
};
extern "C" Buffer* make_buffer();
