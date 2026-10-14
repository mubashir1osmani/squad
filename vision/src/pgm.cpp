#include "quad_vision/image.hpp"

#include "quad_vision/constants.hpp"

#include <fstream>
#include <sstream>
#include <utility>

namespace quad_vision {
namespace {

bool is_ws(unsigned char c) {
  return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f';
}

bool parse_i32(const std::string& text, int& value) {
  if (text.empty() || text.size() > 9) {
    return false;
  }
  int acc = 0;
  for (char c : text) {
    if (c < '0' || c > '9') {
      return false;
    }
    acc = acc * 10 + (c - '0');
  }
  value = acc;
  return true;
}

ImageResult fail(const std::string& error) {
  ImageResult result;
  result.ok = false;
  result.error = error;
  return result;
}

}  // namespace

ImageResult read_pgm(const std::string& path) {
  std::ifstream in(path, std::ios::binary);
  if (!in) {
    return fail("pgm: could not open file");
  }
  in.seekg(0, std::ios::end);
  const std::streamoff end = in.tellg();
  if (end < 0) {
    return fail("pgm: could not read file");
  }
  if (end > kMaxPgmFileBytes) {
    return fail("pgm: file is too large");
  }
  in.seekg(0, std::ios::beg);
  std::string bytes(static_cast<std::size_t>(end), '\0');
  if (end > 0 && !in.read(&bytes[0], end)) {
    return fail("pgm: could not read file");
  }

  const auto* data = reinterpret_cast<const unsigned char*>(bytes.data());
  const std::size_t n = bytes.size();
  std::size_t i = 0;

  auto skip_ws_and_comments = [&]() {
    while (i < n) {
      if (data[i] == '#') {
        while (i < n && data[i] != '\n') {
          ++i;
        }
        continue;
      }
      if (is_ws(data[i])) {
        ++i;
        continue;
      }
      break;
    }
  };

  auto read_token = [&](std::string& token) -> bool {
    skip_ws_and_comments();
    if (i >= n || data[i] == '#') {
      return false;
    }
    token.clear();
    while (i < n && !is_ws(data[i]) && data[i] != '#') {
      token.push_back(static_cast<char>(data[i]));
      ++i;
    }
    return !token.empty();
  };

  std::string magic;
  std::string width_text;
  std::string height_text;
  std::string maxval_text;
  if (!read_token(magic) || !read_token(width_text) || !read_token(height_text) ||
      !read_token(maxval_text)) {
    return fail("pgm: truncated header");
  }
  if (magic != "P5") {
    return fail("pgm: unsupported magic");
  }
  int width = 0;
  int height = 0;
  int maxval = 0;
  if (!parse_i32(width_text, width) || !parse_i32(height_text, height)) {
    return fail("pgm: malformed dimension");
  }
  if (!parse_i32(maxval_text, maxval)) {
    return fail("pgm: malformed maxval");
  }
  if (width < 1 || height < 1 || width > kMaxDimension || height > kMaxDimension) {
    return fail("pgm: unreasonable dimensions");
  }
  if (maxval != 255) {
    return fail("pgm: maxval must be 255");
  }

  // One delimiter ends the header. A CRLF pair counts as that one delimiter.
  // A comment that begins immediately after maxval uses its newline.
  if (i >= n) {
    return fail("pgm: truncated header");
  }
  if (data[i] == '#') {
    while (i < n && data[i] != '\n') {
      ++i;
    }
    if (i >= n || data[i] != '\n') {
      return fail("pgm: truncated header");
    }
    ++i;
  } else if (is_ws(data[i])) {
    const unsigned char mark = data[i];
    ++i;
    if (mark == '\r' && i < n && data[i] == '\n') {
      ++i;
    }
  } else {
    return fail("pgm: missing header delimiter");
  }

  const std::size_t raster = static_cast<std::size_t>(width) * static_cast<std::size_t>(height);
  if (n < i + raster) {
    return fail("pgm: truncated raster");
  }
  if (n != i + raster) {
    return fail("pgm: unexpected trailing bytes");
  }

  ImageResult result;
  result.ok = true;
  result.image.width = width;
  result.image.height = height;
  result.image.stride = width;
  result.image.pixels.assign(data + i, data + i + raster);
  return result;
}

bool write_pgm(const std::string& path, const Image& image, std::string& error) {
  if (image.width < 1 || image.height < 1 || image.stride < image.width) {
    error = "pgm: unreasonable dimensions";
    return false;
  }
  if (image.pixels.size() <
      static_cast<std::size_t>(image.stride) * static_cast<std::size_t>(image.height)) {
    error = "pgm: truncated raster";
    return false;
  }
  std::ostringstream header;
  header << "P5\n" << image.width << ' ' << image.height << "\n255\n";
  std::ofstream out(path, std::ios::binary | std::ios::trunc);
  if (!out) {
    error = "pgm: could not open file";
    return false;
  }
  const std::string head = header.str();
  out.write(head.data(), static_cast<std::streamsize>(head.size()));
  for (int y = 0; y < image.height; ++y) {
    const auto* row = image.pixels.data() +
                      static_cast<std::size_t>(y) * static_cast<std::size_t>(image.stride);
    out.write(reinterpret_cast<const char*>(row), image.width);
  }
  if (!out) {
    error = "pgm: could not write file";
    return false;
  }
  return true;
}

}  // namespace quad_vision
