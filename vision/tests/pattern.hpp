#pragma once

#include <stdint.h>

// Irregular grayscale field used by host tests and the host-timing bench.
// Values stay in 48..188 so a modest brightness offset does not saturate.
inline uint8_t quad_vision_test_sample(int x, int y) {
  const int xx = x + 4096;
  const int yy = y + 4096;
  int value = 48;
  value += ((xx * 13) ^ (yy * 29)) % 80;
  value += (xx % 7) * 5;
  value += (yy % 5) * 4;
  value += (xx * yy) & 15;
  return static_cast<uint8_t>(value);
}
