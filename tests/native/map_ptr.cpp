// SPDX-License-Identifier: Apache-2.0
#include <algorithm>
#include <util/map_ptr.h>
int main() {
    const int values[] = {1, 3, 5};
    android::incfs::map_ptr<int> start(values), finish(values + 3);
    auto found = std::lower_bound(start.iterator(), finish.iterator(), 4,
        [](const auto& entry, int value) { return entry.value() < value; });
    if ((*found).value() != 5) return 1;
    --found;
    if ((*found).value() != 3) return 2;
    auto previous = found--;
    return (*previous).value() == 3 && (*found).value() == 1 ? 0 : 3;
}
