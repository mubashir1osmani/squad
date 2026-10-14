#pragma once

#include "quad_vision/flow_csv.hpp"

#include <string>
#include <vector>

namespace quad_vision {

struct ProcessResult {
  bool ok = false;
  std::string error;
  std::vector<FlowRow> rows;
};

// Reads a manifest, estimates each consecutive pair with the kernel, and
// writes flow CSV only after every pair has been processed. On failure the
// destination file is left untouched.
ProcessResult process_sequence(const std::string& manifest_path,
                               const std::string& output_path);

}  // namespace quad_vision
