//  SPDX-License-Identifier: Apache-2.0
//  Copyright 2026 MOG Robotics OÜ

#pragma once

namespace mathutils {

    template<typename T>
    constexpr T add(T a, T b) {
        return a + b;
    }

    template<typename T>
    constexpr T multiply(T a, T b) {
        return a * b;
    }

    constexpr double pi() {
        return 3.14159265359;
    }

    constexpr bool is_even(int n) {
        return n % 2 == 0;
    }
}
