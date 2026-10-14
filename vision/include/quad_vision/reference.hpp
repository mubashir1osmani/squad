#pragma once

#include "quad_vision/estimate.hpp"

namespace quad_vision {

// Same numeric contract as estimate_kernel. This reference may allocate.
// It is the comparison implementation for host tests, not the HLS starting point.
KernelStatus estimate_reference(const uint8_t* prev,
                                const uint8_t* curr,
                                int width,
                                int height,
                                int prev_stride,
                                int curr_stride,
                                FlowEstimate* out);

}  // namespace quad_vision
