/* Copyright 2026 Madara273
 * DroidForge compatibility declarations for AOSP host tools on glibc.
 * AOSP's C++ code uses bionic atomic typedefs and Darwin availability syntax.
 * SPDX-License-Identifier: Apache-2.0
 */
#pragma once
#include <stdint.h>
#include <limits.h>
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
using std::atomic_int_least16_t;
using std::atomic_int_least32_t;
using std::atomic_int_least64_t;
using std::memory_order;
using std::memory_order_relaxed;
using std::memory_order_consume;
using std::memory_order_acquire;
using std::memory_order_release;
using std::memory_order_acq_rel;
using std::memory_order_seq_cst;
#endif
#ifndef __has_builtin
#define __has_builtin(x) 0
#endif
#if !__has_builtin(__builtin_available)
#define __builtin_available(...) 0
#endif
