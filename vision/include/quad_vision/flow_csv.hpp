#pragma once

#include "quad_vision/estimate.hpp"

#include <cstdint>
#include <string>
#include <vector>

namespace quad_vision {

struct FlowRow {
  std::uint64_t t_us = 0;
  std::uint64_t dt_us = 0;
  std::int32_t dx_px = 0;
  std::int32_t dy_px = 0;
  std::int32_t quality = 0;
  std::int32_t valid = 0;
};

// Header `t_us,dt_us,dx_px,dy_px,quality,valid`, LF separators, no spaces.
// Invalid rows are written as zero displacement and quality.
std::string format_flow_csv(const std::vector<FlowRow>& rows);

}  // namespace quad_vision
