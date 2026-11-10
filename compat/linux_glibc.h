/* DroidForge compatibility declarations for AOSP host tools on glibc.
 * AOSP's C++ code uses bionic atomic typedefs and Darwin availability syntax.
 * SPDX-License-Identifier: Apache-2.0
 */
#pragma once
#include <stdint.h>
#ifdef __cplusplus
#include <cstring>
#include <memory>
#include <limits>
#include <atomic>
using std::atomic_bool;
using std::atomic_int;
using std::atomic_uint;
using std::atomic_uint_least16_t;
using std::atomic_uint_least32_t;
using std::atomic_uint_least64_t;
#endif
#ifndef __has_builtin
#define __has_builtin(x) 0
#endif
#if !__has_builtin(__builtin_available)
#define __builtin_available(...) 0
#endif
