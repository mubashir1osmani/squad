#pragma once

#include <stdint.h>

namespace quad_vision {

// Status of a call. Ok with valid==0 is a successful rejection (no flow),
// not an I/O failure.
enum class KernelStatus : int {
  Ok = 0,
  BadArgument = 1,
  UnsupportedSize = 2,
};

// Invalid estimates keep dx_px, dy_px, and quality at 0.
// quality is an algorithm score in [0, 255], not a probability.
struct FlowEstimate {
  int32_t dx_px;
  int32_t dy_px;
  int32_t quality;
  int32_t valid;
};

}  // namespace quad_vision
