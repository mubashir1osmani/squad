#pragma once

#include "quad_vision/estimate.hpp"

namespace quad_vision {

// Allocation-free global translation estimate for one frame pair.
//
// prev and curr point at caller-owned 8-bit grayscale planes. The core does
// not allocate, resize, or free memory. width and height must be in
// [kMinDimension, kMaxDimension]. Each stride is >= width and <= kMaxStride.
// The caller owns at least stride * height bytes for each plane.
//
// On BadArgument or UnsupportedSize, *out is cleared to an invalid estimate
// when out is non-null. A null out pointer returns BadArgument.
//
// Host compilation of this function is not HLS synthesis.
KernelStatus estimate_kernel(const uint8_t* prev,
                             const uint8_t* curr,
                             int width,
                             int height,
                             int prev_stride,
                             int curr_stride,
                             FlowEstimate* out);

}  // namespace quad_vision
