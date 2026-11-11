// SPDX-License-Identifier: Apache-2.0
#include <cerrno>
#include <cstring>
extern "C" int posix_strerror_r(int, char*, size_t);
int main() {
    char text[128] = {};
    if (posix_strerror_r(ENOENT, text, sizeof(text)) != 0 || text[0] == '\0') return 1;
    char tiny[1];
    return posix_strerror_r(ENOENT, tiny, sizeof(tiny)) == ERANGE ? 0 : 2;
}
