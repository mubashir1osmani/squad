#include "quad_vision/flow_csv.hpp"

#include <sstream>

namespace quad_vision {

std::string format_flow_csv(const std::vector<FlowRow>& rows) {
  std::ostringstream out;
  out.imbue(std::locale::classic());
  out << "t_us,dt_us,dx_px,dy_px,quality,valid\n";
  for (const FlowRow& row : rows) {
    const bool invalid = row.valid == 0;
    const std::int32_t dx = invalid ? 0 : row.dx_px;
    const std::int32_t dy = invalid ? 0 : row.dy_px;
    const std::int32_t quality = invalid ? 0 : row.quality;
    const std::int32_t valid = invalid ? 0 : 1;
    out << row.t_us << ',' << row.dt_us << ',' << dx << ',' << dy << ',' << quality << ','
        << valid << '\n';
  }
  return out.str();
}

}  // namespace quad_vision
