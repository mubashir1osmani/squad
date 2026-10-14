#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace quad_vision {

struct ManifestFrame {
  std::uint64_t t_us = 0;
  std::string filename;
};

struct Manifest {
  std::string directory;
  std::vector<ManifestFrame> frames;
};

struct ManifestResult {
  bool ok = false;
  std::string error;
  Manifest manifest;
};

// Lexical check only. Rejects absolute paths, backslashes, and ".." segments.
// Does not resolve symlinks.
bool filename_is_safe(const std::string& filename, std::string& error);

// Parses the manifest file. Does not open the referenced images.
// Requires the header `t_us,filename`, at least two rows, and strictly
// increasing nonnegative microsecond timestamps.
ManifestResult read_manifest(const std::string& path);

}  // namespace quad_vision
