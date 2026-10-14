#include "quad_vision/process.hpp"

#include <iostream>
#include <string>

namespace {

void print_usage(std::ostream& out) {
  out << "Usage: quad-vision --manifest PATH --output PATH\n"
         "\n"
         "Read a PGM sequence manifest and write optical-flow CSV.\n"
         "dx_px and dy_px are image-plane pixel displacements (+x right, +y down).\n"
         "They are not aircraft velocity, position, or a control command.\n";
}

}  // namespace

int main(int argc, char** argv) {
  std::string manifest;
  std::string output;
  bool saw_manifest = false;
  bool saw_output = false;

  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--help" || arg == "-h") {
      print_usage(std::cout);
      return 0;
    }
    if (arg == "--manifest") {
      if (i + 1 >= argc) {
        std::cerr << "quad-vision: missing value for --manifest\n";
        return 1;
      }
      if (saw_manifest) {
        std::cerr << "quad-vision: duplicate --manifest\n";
        return 1;
      }
      manifest = argv[++i];
      saw_manifest = true;
      continue;
    }
    if (arg == "--output") {
      if (i + 1 >= argc) {
        std::cerr << "quad-vision: missing value for --output\n";
        return 1;
      }
      if (saw_output) {
        std::cerr << "quad-vision: duplicate --output\n";
        return 1;
      }
      output = argv[++i];
      saw_output = true;
      continue;
    }
    std::cerr << "quad-vision: unknown argument " << arg << "\n";
    print_usage(std::cerr);
    return 1;
  }

  if (!saw_manifest || !saw_output) {
    std::cerr << "quad-vision: --manifest and --output are required\n";
    print_usage(std::cerr);
    return 1;
  }

  const quad_vision::ProcessResult result = quad_vision::process_sequence(manifest, output);
  if (!result.ok) {
    std::cerr << "quad-vision: " << result.error << "\n";
    return 1;
  }

  int valid_count = 0;
  for (const quad_vision::FlowRow& row : result.rows) {
    if (row.valid == 1) {
      ++valid_count;
    }
  }
  const int pairs = static_cast<int>(result.rows.size());
  std::cout << "pairs=" << pairs << " valid=" << valid_count << " invalid=" << (pairs - valid_count)
            << "\n";
  return 0;
}
