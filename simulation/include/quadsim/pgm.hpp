#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace quadsim {

struct Image {
    int width = 0;
    int height = 0;
    std::vector<std::uint8_t> pixels;
};

// P5, 8-bit, maxval 255. comment is a single header line without a leading '#'.
void write_pgm(const std::string& path, const Image& image, const std::string& comment);
Image read_pgm(const std::string& path);

}  // namespace quadsim
