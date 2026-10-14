#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace quad_vision {

struct Image {
  int width = 0;
  int height = 0;
  int stride = 0;
  std::vector<std::uint8_t> pixels;

  const std::uint8_t* data() const { return pixels.empty() ? nullptr : pixels.data(); }
  std::uint8_t* data() { return pixels.empty() ? nullptr : pixels.data(); }
};

struct ImageResult {
  bool ok = false;
  std::string error;
  Image image;
};

// P5 binary grayscale, maxval 255. Dimensions must be in 1..kMaxDimension.
// The single whitespace byte after maxval ends the header. Comments are
// accepted between header tokens. Trailing bytes after the raster are rejected.
ImageResult read_pgm(const std::string& path);

// Packs the first `width` bytes of each row. Uses LF header separators.
bool write_pgm(const std::string& path, const Image& image, std::string& error);

}  // namespace quad_vision
