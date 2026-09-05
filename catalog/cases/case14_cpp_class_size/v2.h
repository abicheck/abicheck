class Buffer {
public:
    Buffer();
    int size();
private:
    char data[128];
};
extern "C" Buffer* make_buffer();
