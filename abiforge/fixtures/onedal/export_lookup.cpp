// SPDX-License-Identifier: Apache-2.0
#include <dlfcn.h>
#include <filesystem>
#include <iostream>
#include <string>

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    void* provider = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!provider) {
        std::cerr << dlerror() << '\n';
        return 2;
    }
    std::string name;
    while (std::getline(std::cin, name)) {
        dlerror();
        void* address = dlsym(provider, name.c_str());
        Dl_info owner{};
        if (dlerror() || !dladdr(address, &owner) ||
            !std::filesystem::equivalent(owner.dli_fname, argv[1])) {
            std::cerr << "Missing or unexpected owner for " << name << '\n';
            return 1;
        }
    }
    return std::cin.eof() ? 0 : 2;
}
