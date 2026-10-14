#include "quad_vision/constants.hpp"
#include "quad_vision/flow_csv.hpp"
#include "quad_vision/image.hpp"
#include "quad_vision/kernel.hpp"
#include "quad_vision/manifest.hpp"
#include "quad_vision/process.hpp"
#include "quad_vision/reference.hpp"

#include "pattern.hpp"

#include <sys/wait.h>

#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

int g_checks = 0;
int g_failures = 0;

void fail(const char* file, int line, const std::string& message) {
  std::cerr << file << ":" << line << ": " << message << "\n";
  ++g_failures;
}

#define CHECK(cond)                                                       \
  do {                                                                    \
    ++g_checks;                                                           \
    if (!(cond)) {                                                        \
      fail(__FILE__, __LINE__, std::string("check failed: ") + #cond);   \
    }                                                                     \
  } while (0)

#define CHECK_EQ(a, b)                                                                  \
  do {                                                                                  \
    ++g_checks;                                                                         \
    const auto left_value = (a);                                                        \
    const auto right_value = (b);                                                       \
    if (!(left_value == right_value)) {                                                 \
      std::ostringstream message;                                                       \
      message << "expected " #a " == " #b " (" << left_value << " vs " << right_value  \
              << ")";                                                                   \
      fail(__FILE__, __LINE__, message.str());                                          \
    }                                                                                   \
  } while (0)

void write_text(const std::filesystem::path& path, const std::string& text) {
  std::filesystem::create_directories(path.parent_path());
  std::ofstream out(path, std::ios::binary | std::ios::trunc);
  out << text;
}

std::string read_text(const std::filesystem::path& path) {
  std::ifstream in(path, std::ios::binary);
  std::ostringstream buffer;
  buffer << in.rdbuf();
  return buffer.str();
}

quad_vision::Image render(int width, int height, int stride, int x0, int y0) {
  quad_vision::Image image;
  image.width = width;
  image.height = height;
  image.stride = stride;
  image.pixels.assign(static_cast<std::size_t>(stride) * static_cast<std::size_t>(height), 0xA5);
  for (int y = 0; y < height; ++y) {
    for (int x = 0; x < width; ++x) {
      image.pixels[static_cast<std::size_t>(y * stride + x)] =
          quad_vision_test_sample(x0 + x, y0 + y);
    }
  }
  return image;
}

quad_vision::Image render_packed(int width, int height, int shift_x, int shift_y) {
  return render(width, height, width, -shift_x, -shift_y);
}

void expect_flow(const char* label,
                 const quad_vision::FlowEstimate& estimate,
                 int dx,
                 int dy,
                 int quality,
                 int valid) {
  ++g_checks;
  if (estimate.dx_px != dx || estimate.dy_px != dy || estimate.quality != quality ||
      estimate.valid != valid) {
    std::ostringstream message;
    message << label << " got dx=" << estimate.dx_px << " dy=" << estimate.dy_px
            << " quality=" << estimate.quality << " valid=" << estimate.valid
            << " expected dx=" << dx << " dy=" << dy << " quality=" << quality
            << " valid=" << valid;
    fail(__FILE__, __LINE__, message.str());
  }
}

void expect_both(const quad_vision::Image& prev,
                 const quad_vision::Image& curr,
                 int dx,
                 int dy,
                 int quality,
                 int valid,
                 const char* label) {
  quad_vision::FlowEstimate kernel{};
  quad_vision::FlowEstimate reference{};
  const auto kernel_status = quad_vision::estimate_kernel(
      prev.data(), curr.data(), prev.width, prev.height, prev.stride, curr.stride, &kernel);
  const auto reference_status = quad_vision::estimate_reference(
      prev.data(), curr.data(), prev.width, prev.height, prev.stride, curr.stride, &reference);
  CHECK_EQ(static_cast<int>(kernel_status), static_cast<int>(quad_vision::KernelStatus::Ok));
  CHECK_EQ(static_cast<int>(reference_status), static_cast<int>(quad_vision::KernelStatus::Ok));
  expect_flow(label, kernel, dx, dy, quality, valid);
  expect_flow(std::string(label).append(" reference").c_str(), reference, dx, dy, quality, valid);
}

void expect_shift(int width, int height, int shift_x, int shift_y) {
  const quad_vision::Image prev = render_packed(width, height, 0, 0);
  const quad_vision::Image curr = render_packed(width, height, shift_x, shift_y);
  std::ostringstream label;
  label << width << "x" << height << " shift " << shift_x << "," << shift_y;
  const std::string text = label.str();
  if (shift_x > quad_vision::kSearchRadius || shift_y > quad_vision::kSearchRadius ||
      shift_x < -quad_vision::kSearchRadius || shift_y < -quad_vision::kSearchRadius) {
    expect_both(prev, curr, 0, 0, 0, 0, text.c_str());
  } else {
    expect_both(prev, curr, shift_x, shift_y, 255, 1, text.c_str());
  }
}

void test_shifts() {
  const int shifts[][2] = {
      {0, 0},   {3, 0},  {-4, 0}, {0, 5},  {0, -2}, {2, -3}, {-5, 4}, {8, 0},
      {-8, 0},  {0, 8},  {0, -8}, {8, -8}, {7, -7}, {-1, 1}, {9, 0},  {-9, 0},
      {0, 9},   {0, -9}, {9, -2}, {3, 9},  {-8, 8}, {1, -1},
  };
  for (const auto& shift : shifts) {
    expect_shift(64, 64, shift[0], shift[1]);
  }
  expect_shift(32, 32, 0, 0);
  expect_shift(32, 32, 3, -1);
  expect_shift(32, 32, 8, 0);
  expect_shift(32, 32, 9, 0);
  expect_shift(80, 64, 2, -3);
  expect_shift(96, 80, -6, 4);
  expect_shift(128, 128, 0, 0);
  expect_shift(128, 128, 4, -5);
  expect_shift(128, 128, -8, 8);
  expect_shift(128, 128, 9, 1);
}

void test_brightness_and_noise() {
  const int shift_x = 2;
  const int shift_y = -3;
  quad_vision::Image prev = render_packed(64, 64, 0, 0);
  quad_vision::Image curr = render_packed(64, 64, shift_x, shift_y);
  for (std::uint8_t& pixel : curr.pixels) {
    pixel = static_cast<std::uint8_t>(pixel + 30);
  }
  expect_both(prev, curr, shift_x, shift_y, 255, 1, "brightness +30");

  curr = render_packed(64, 64, shift_x, shift_y);
  for (int y = 0; y < curr.height; ++y) {
    for (int x = 0; x < curr.width; ++x) {
      const int noise = ((x * 17 + y * 13 + 3) % 5) - 2;
      int value = static_cast<int>(curr.pixels[static_cast<std::size_t>(y * curr.stride + x)]) + noise;
      if (value < 0) {
        value = 0;
      }
      if (value > 255) {
        value = 255;
      }
      curr.pixels[static_cast<std::size_t>(y * curr.stride + x)] = static_cast<std::uint8_t>(value);
    }
  }
  expect_both(prev, curr, shift_x, shift_y, 255, 1, "deterministic noise");

  for (int y = 0; y < curr.height; ++y) {
    for (int x = 0; x < curr.width; ++x) {
      int value = static_cast<int>(curr.pixels[static_cast<std::size_t>(y * curr.stride + x)]) + 20;
      if (value > 255) {
        value = 255;
      }
      curr.pixels[static_cast<std::size_t>(y * curr.stride + x)] = static_cast<std::uint8_t>(value);
    }
  }
  expect_both(prev, curr, shift_x, shift_y, 255, 1, "noise plus brightness");
}

void test_rejections() {
  quad_vision::Image flat_prev = render_packed(64, 64, 0, 0);
  quad_vision::Image flat_curr = render_packed(64, 64, 0, 0);
  for (std::uint8_t& pixel : flat_prev.pixels) {
    pixel = 100;
  }
  for (std::uint8_t& pixel : flat_curr.pixels) {
    pixel = 100;
  }
  expect_both(flat_prev, flat_curr, 0, 0, 0, 0, "flat pair");

  quad_vision::Image textured = render_packed(64, 64, 0, 0);
  expect_both(textured, flat_curr, 0, 0, 0, 0, "textured then flat");
  expect_both(flat_prev, textured, 0, 0, 0, 0, "flat then textured");

  quad_vision::Image spike_prev = flat_prev;
  quad_vision::Image spike_curr = flat_curr;
  spike_prev.pixels[static_cast<std::size_t>(30 * spike_prev.stride + 30)] = 200;
  spike_curr.pixels[static_cast<std::size_t>(30 * spike_curr.stride + 30)] = 200;
  expect_both(spike_prev, spike_curr, 0, 0, 0, 0, "single spike");

  quad_vision::Image gradient = render_packed(64, 64, 0, 0);
  for (int y = 0; y < gradient.height; ++y) {
    for (int x = 0; x < gradient.width; ++x) {
      gradient.pixels[static_cast<std::size_t>(y * gradient.stride + x)] =
          static_cast<std::uint8_t>(80 + x / 16);
    }
  }
  expect_both(gradient, gradient, 0, 0, 0, 0, "low contrast gradient");

  auto periodic = [](int x, int y) -> std::uint8_t {
    const int base = ((x / 4) & 1) != 0 ? 50 : 200;
    return static_cast<std::uint8_t>(base + (y & 7));
  };
  quad_vision::Image bars = render_packed(64, 64, 0, 0);
  for (int y = 0; y < bars.height; ++y) {
    for (int x = 0; x < bars.width; ++x) {
      bars.pixels[static_cast<std::size_t>(y * bars.stride + x)] = periodic(x, y);
    }
  }
  expect_both(bars, bars, 0, 0, 0, 0, "periodic ambiguity");

  // Equal and opposite translations on two block columns. The middle columns
  // are flattened in the previous frame so they do not vote.
  constexpr int kSide = 80;
  constexpr int kSplit = 40;
  quad_vision::Image split_prev = render(kSide, kSide, kSide, 0, 0);
  quad_vision::Image split_curr = render(kSide, kSide, kSide, 0, 0);
  for (int y = 0; y < kSide; ++y) {
    for (int x = 0; x < kSide; ++x) {
      split_prev.pixels[static_cast<std::size_t>(y * kSide + x)] = quad_vision_test_sample(x, y);
      if (x < kSplit) {
        split_curr.pixels[static_cast<std::size_t>(y * kSide + x)] =
            quad_vision_test_sample(x - 2, y);
      } else {
        split_curr.pixels[static_cast<std::size_t>(y * kSide + x)] =
            quad_vision_test_sample(x + 2, y);
      }
    }
  }
  for (int y = 0; y < kSide; ++y) {
    for (int x = 24; x < 56; ++x) {
      split_prev.pixels[static_cast<std::size_t>(y * kSide + x)] = 100;
    }
  }
  expect_both(split_prev, split_curr, 0, 0, 0, 0, "disagreeing regions");
}

void test_partial_texture_quality() {
  quad_vision::Image prev = render_packed(64, 64, 0, 0);
  quad_vision::Image curr = render_packed(64, 64, 0, 0);
  for (int y = 8; y < 24; ++y) {
    for (int x = 8; x < 24; ++x) {
      prev.pixels[static_cast<std::size_t>(y * prev.stride + x)] = 128;
      curr.pixels[static_cast<std::size_t>(y * curr.stride + x)] = 128;
    }
  }
  // 3x3 grid, one block abstains, eight agree: quality = 255 * 8 / 9.
  expect_both(prev, curr, 0, 0, (255 * 8) / 9, 1, "one flat block");
}

void test_stride_and_arguments() {
  constexpr int kWidth = 64;
  constexpr int kHeight = 48;
  constexpr int kStride = kWidth + 8;
  const quad_vision::Image prev = render(kWidth, kHeight, kStride, 0, 0);
  const quad_vision::Image curr = render(kWidth, kHeight, kStride + 3, -3, 2);
  expect_both(prev, curr, 3, -2, 255, 1, "padded stride");
  ++g_checks;
  bool padding_intact = true;
  for (int y = 0; y < prev.height; ++y) {
    for (int x = prev.width; x < prev.stride; ++x) {
      if (prev.pixels[static_cast<std::size_t>(y * prev.stride + x)] != 0xA5) {
        padding_intact = false;
      }
    }
  }
  if (!padding_intact) {
    fail(__FILE__, __LINE__, "kernel or reference wrote into the previous-frame padding");
  }

  quad_vision::FlowEstimate estimate{};
  CHECK_EQ(static_cast<int>(quad_vision::estimate_kernel(nullptr, curr.data(), 64, 64, 64, 64,
                                                         &estimate)),
           static_cast<int>(quad_vision::KernelStatus::BadArgument));
  CHECK_EQ(estimate.valid, 0);
  CHECK_EQ(estimate.dx_px, 0);
  CHECK_EQ(estimate.quality, 0);
  CHECK_EQ(static_cast<int>(quad_vision::estimate_kernel(prev.data(), curr.data(), 64, 64, 64, 64,
                                                         nullptr)),
           static_cast<int>(quad_vision::KernelStatus::BadArgument));
  CHECK_EQ(static_cast<int>(quad_vision::estimate_reference(prev.data(), nullptr, 64, 64, 64, 64,
                                                            &estimate)),
           static_cast<int>(quad_vision::KernelStatus::BadArgument));
  CHECK_EQ(static_cast<int>(quad_vision::estimate_kernel(prev.data(), curr.data(), 16, 64, 16, 16,
                                                         &estimate)),
           static_cast<int>(quad_vision::KernelStatus::UnsupportedSize));
  CHECK_EQ(static_cast<int>(quad_vision::estimate_kernel(prev.data(), curr.data(), 129, 64, 129, 129,
                                                         &estimate)),
           static_cast<int>(quad_vision::KernelStatus::UnsupportedSize));
  CHECK_EQ(static_cast<int>(quad_vision::estimate_reference(prev.data(), curr.data(), 64, 0, 64, 64,
                                                            &estimate)),
           static_cast<int>(quad_vision::KernelStatus::UnsupportedSize));
  const quad_vision::Image tight = render_packed(32, 32, 0, 0);
  CHECK_EQ(static_cast<int>(quad_vision::estimate_kernel(tight.data(), tight.data(), 32, 32, 31, 32,
                                                         &estimate)),
           static_cast<int>(quad_vision::KernelStatus::BadArgument));
}

bool contains(const std::string& text, const std::string& needle) {
  return text.find(needle) != std::string::npos;
}

bool contains_token(const std::string& text, const std::string& token) {
  std::size_t pos = 0;
  while ((pos = text.find(token, pos)) != std::string::npos) {
    const bool left = pos == 0 ||
                      !(std::isalnum(static_cast<unsigned char>(text[pos - 1])) || text[pos - 1] == '_');
    const std::size_t end = pos + token.size();
    const bool right = end >= text.size() ||
                       !(std::isalnum(static_cast<unsigned char>(text[end])) || text[end] == '_');
    if (left && right) {
      return true;
    }
    pos = end;
  }
  return false;
}

void test_kernel_source_is_bounded() {
  const std::filesystem::path root{QUAD_VISION_SOURCE_DIR};
  const std::string kernel = read_text(root / "src" / "kernel.cpp");
  const std::string header = read_text(root / "include" / "quad_vision" / "kernel.hpp");
  const std::string reference = read_text(root / "src" / "reference.cpp");
  CHECK(!kernel.empty());
  CHECK(!header.empty());
  const std::string forbidden[] = {"malloc", "calloc", "realloc", "vector", "string", "throw",
                                   "float", "double"};
  for (const std::string& token : forbidden) {
    CHECK(!contains_token(kernel, token));
    CHECK(!contains_token(header, token));
  }
  CHECK(!contains(kernel, "new "));
  CHECK(!contains(kernel, "new["));
  CHECK(!contains(header, "new "));
  CHECK(contains(reference, "std::vector"));
}

void test_pgm() {
  const std::filesystem::path dir =
      std::filesystem::temp_directory_path() / "quad-vision-tests" / "pgm";
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);

  quad_vision::Image image = render_packed(32, 32, 0, 0);
  image.pixels[0] = 0x0A;
  image.pixels[1] = 0x0D;
  image.pixels[2] = 0x20;
  image.pixels[3] = '#';
  image.pixels[4] = 0;
  image.pixels[5] = 255;
  std::string error;
  CHECK(quad_vision::write_pgm((dir / "round.pgm").string(), image, error));
  const quad_vision::ImageResult round = quad_vision::read_pgm((dir / "round.pgm").string());
  CHECK(round.ok);
  CHECK_EQ(round.image.width, 32);
  CHECK_EQ(round.image.height, 32);
  CHECK(round.image.pixels == image.pixels);

  std::string raw = read_text(dir / "round.pgm");
  const std::string header = "P5\n# a comment\n32  32\n# tail\n255\n";
  const std::size_t raster_at = raw.find("255\n");
  CHECK(raster_at != std::string::npos);
  const std::string raster = raw.substr(raster_at + 4);
  write_text(dir / "comment.pgm", header + raster);
  const quad_vision::ImageResult commented = quad_vision::read_pgm((dir / "comment.pgm").string());
  CHECK(commented.ok);
  CHECK(commented.image.pixels == image.pixels);

  write_text(dir / "crlf.pgm", std::string("P5\r\n32 32\r\n255\r\n") + raster);
  const quad_vision::ImageResult crlf = quad_vision::read_pgm((dir / "crlf.pgm").string());
  CHECK(crlf.ok);
  CHECK(crlf.image.pixels == image.pixels);

  write_text(dir / "hashmax.pgm", std::string("P5\n32 32\n255#end\n") + raster);
  const quad_vision::ImageResult hashmax = quad_vision::read_pgm((dir / "hashmax.pgm").string());
  CHECK(hashmax.ok);
  CHECK(hashmax.image.pixels == image.pixels);

  write_text(dir / "ascii.pgm", "P2\n32 32\n255\n0\n");
  const quad_vision::ImageResult ascii = quad_vision::read_pgm((dir / "ascii.pgm").string());
  CHECK(!ascii.ok);
  CHECK(contains(ascii.error, "unsupported magic"));

  write_text(dir / "color.pgm", std::string("P6\n32 32\n255\n") + raster);
  CHECK(contains(quad_vision::read_pgm((dir / "color.pgm").string()).error, "unsupported magic"));

  write_text(dir / "deep.pgm", std::string("P5\n32 32\n65535\n") + raster + raster);
  CHECK(contains(quad_vision::read_pgm((dir / "deep.pgm").string()).error, "maxval must be 255"));

  write_text(dir / "lowmax.pgm", std::string("P5\n32 32\n15\n") + raster);
  CHECK(contains(quad_vision::read_pgm((dir / "lowmax.pgm").string()).error, "maxval must be 255"));

  write_text(dir / "short.pgm", std::string("P5\n32 32\n255\n") + raster.substr(0, 10));
  CHECK(contains(quad_vision::read_pgm((dir / "short.pgm").string()).error, "truncated raster"));

  write_text(dir / "extra.pgm", std::string("P5\n32 32\n255\n") + raster + "\n");
  CHECK(contains(quad_vision::read_pgm((dir / "extra.pgm").string()).error, "unexpected trailing bytes"));

  write_text(dir / "zero.pgm", "P5\n0 32\n255\n");
  CHECK(contains(quad_vision::read_pgm((dir / "zero.pgm").string()).error, "unreasonable dimensions"));

  write_text(dir / "huge.pgm", "P5\n100000 100000\n255\n");
  CHECK(contains(quad_vision::read_pgm((dir / "huge.pgm").string()).error, "unreasonable dimensions"));

  write_text(dir / "badwidth.pgm", "P5\nabc 32\n255\n");
  CHECK(contains(quad_vision::read_pgm((dir / "badwidth.pgm").string()).error, "malformed dimension"));

  write_text(dir / "empty.pgm", "");
  CHECK(contains(quad_vision::read_pgm((dir / "empty.pgm").string()).error, "truncated header"));

  write_text(dir / "fat.pgm", std::string(static_cast<std::size_t>(quad_vision::kMaxPgmFileBytes) + 1, 'P'));
  CHECK(contains(quad_vision::read_pgm((dir / "fat.pgm").string()).error, "file is too large"));

  CHECK(contains(quad_vision::read_pgm((dir / "missing.pgm").string()).error, "could not open"));
}

void test_manifest_parser() {
  const std::filesystem::path dir =
      std::filesystem::temp_directory_path() / "quad-vision-tests" / "manifest";
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);

  write_text(dir / "ok.csv", "t_us,filename\n0,./frame_000.pgm\n20000,sub/frame_001.pgm\n");
  const quad_vision::ManifestResult ok = quad_vision::read_manifest((dir / "ok.csv").string());
  CHECK(ok.ok);
  CHECK_EQ(ok.manifest.frames.size(), static_cast<std::size_t>(2));
  CHECK_EQ(ok.manifest.frames[1].t_us, static_cast<std::uint64_t>(20000));
  CHECK_EQ(ok.manifest.frames[1].filename, std::string("sub/frame_001.pgm"));

  write_text(dir / "crlf.csv", "t_us,filename\r\n0,a.pgm\r\n1,b.pgm\r\n");
  CHECK(quad_vision::read_manifest((dir / "crlf.csv").string()).ok);

  write_text(dir / "zeros.csv", "t_us,filename\n0,a.pgm\n00,b.pgm\n");
  CHECK(contains(quad_vision::read_manifest((dir / "zeros.csv").string()).error,
                 "strictly increasing"));

  write_text(dir / "back.csv", "t_us,filename\n10,a.pgm\n40000,b.pgm\n39999,c.pgm\n");
  CHECK(contains(quad_vision::read_manifest((dir / "back.csv").string()).error,
                 "strictly increasing"));

  write_text(dir / "neg.csv", "t_us,filename\n-1,a.pgm\n2,b.pgm\n");
  CHECK(contains(quad_vision::read_manifest((dir / "neg.csv").string()).error, "malformed timestamp"));

  write_text(dir / "nan.csv", "t_us,filename\nnan,a.pgm\n2,b.pgm\n");
  CHECK(contains(quad_vision::read_manifest((dir / "nan.csv").string()).error, "malformed timestamp"));

  write_text(dir / "plus.csv", "t_us,filename\n+1,a.pgm\n2,b.pgm\n");
  CHECK(contains(quad_vision::read_manifest((dir / "plus.csv").string()).error, "malformed timestamp"));

  write_text(dir / "overflow.csv",
             "t_us,filename\n0,a.pgm\n18446744073709551616,b.pgm\n");
  CHECK(contains(quad_vision::read_manifest((dir / "overflow.csv").string()).error,
                 "malformed timestamp"));

  write_text(dir / "max.csv",
             "t_us,filename\n0,a.pgm\n18446744073709551615,b.pgm\n");
  const quad_vision::ManifestResult max_time =
      quad_vision::read_manifest((dir / "max.csv").string());
  CHECK(max_time.ok);
  CHECK_EQ(max_time.manifest.frames[1].t_us, static_cast<std::uint64_t>(-1));

  write_text(dir / "abs.csv", "t_us,filename\n0,/tmp/a.pgm\n1,b.pgm\n");
  CHECK(contains(quad_vision::read_manifest((dir / "abs.csv").string()).error, "absolute path"));

  write_text(dir / "parent.csv", "t_us,filename\n0,../secret.pgm\n1,b.pgm\n");
  CHECK(contains(quad_vision::read_manifest((dir / "parent.csv").string()).error, "parent traversal"));

  write_text(dir / "nested.csv", "t_us,filename\n0,foo/../../secret.pgm\n1,b.pgm\n");
  CHECK(contains(quad_vision::read_manifest((dir / "nested.csv").string()).error, "parent traversal"));

  write_text(dir / "slash.csv", "t_us,filename\n0,foo\\\\bar.pgm\n1,b.pgm\n");
  CHECK(contains(quad_vision::read_manifest((dir / "slash.csv").string()).error, "backslash"));

  write_text(dir / "header.csv", "time,file\n0,a.pgm\n1,b.pgm\n");
  CHECK(contains(quad_vision::read_manifest((dir / "header.csv").string()).error,
                 "expected header"));

  write_text(dir / "one.csv", "t_us,filename\n0,a.pgm\n");
  CHECK(contains(quad_vision::read_manifest((dir / "one.csv").string()).error,
                 "need at least two frames"));

  write_text(dir / "extra.csv", "t_us,filename\n0,a.pgm,1\n1,b.pgm\n");
  CHECK(contains(quad_vision::read_manifest((dir / "extra.csv").string()).error, "expected two fields"));

  write_text(dir / "blank.csv", "t_us,filename\n\n0,a.pgm\n1,b.pgm\n");
  CHECK(contains(quad_vision::read_manifest((dir / "blank.csv").string()).error, "empty line"));

  write_text(dir / "space.csv", "t_us,filename\n0,frame 1.pgm\n1,b.pgm\n");
  CHECK(contains(quad_vision::read_manifest((dir / "space.csv").string()).error, "space"));

  std::string path_error;
  CHECK(quad_vision::filename_is_safe("./a.pgm", path_error));
  CHECK(quad_vision::filename_is_safe("sub/a.pgm", path_error));
  CHECK(!quad_vision::filename_is_safe("foo//a.pgm", path_error));
  CHECK(!quad_vision::filename_is_safe("C:/a.pgm", path_error));
}

std::string shell_quote(const std::string& text) {
  return "\"" + text + "\"";
}

int run_shell(const std::string& command, std::string& stdout_text, std::string& stderr_text,
              const std::filesystem::path& dir) {
  const std::filesystem::path out_path = dir / "stdout.txt";
  const std::filesystem::path err_path = dir / "stderr.txt";
  std::filesystem::remove(out_path);
  std::filesystem::remove(err_path);
  const std::string full = "LC_ALL=C " + command + " >" + shell_quote(out_path.string()) + " 2>" +
                           shell_quote(err_path.string());
  const int status = std::system(full.c_str());
  stdout_text = read_text(out_path);
  stderr_text = read_text(err_path);
  if (status < 0 || !WIFEXITED(status)) {
    return -1;
  }
  return WEXITSTATUS(status);
}

struct ParsedFlow {
  bool ok = false;
  std::string error;
  std::vector<quad_vision::FlowRow> rows;
};

ParsedFlow parse_flow(const std::string& text) {
  ParsedFlow parsed;
  if (text.empty() || text.back() != '\n' || text.find('\r') != std::string::npos) {
    parsed.error = "newline contract";
    return parsed;
  }
  std::size_t pos = 0;
  bool header = true;
  while (pos < text.size()) {
    const std::size_t eol = text.find('\n', pos);
    const std::string line = text.substr(pos, eol - pos);
    pos = eol + 1;
    if (line.empty() || line.find(' ') != std::string::npos) {
      parsed.error = "blank or space";
      return parsed;
    }
    if (header) {
      if (line != "t_us,dt_us,dx_px,dy_px,quality,valid") {
        parsed.error = "header";
        return parsed;
      }
      header = false;
      continue;
    }
    std::vector<std::string> fields;
    std::size_t start = 0;
    for (std::size_t i = 0; i <= line.size(); ++i) {
      if (i == line.size() || line[i] == ',') {
        fields.push_back(line.substr(start, i - start));
        start = i + 1;
      }
    }
    if (fields.size() != 6) {
      parsed.error = "field count";
      return parsed;
    }
    auto parse_i64 = [](const std::string& field, std::int64_t& value) -> bool {
      if (field.empty()) {
        return false;
      }
      std::size_t index = 0;
      int sign = 1;
      if (field[0] == '-') {
        sign = -1;
        index = 1;
        if (field.size() == 1) {
          return false;
        }
      }
      std::uint64_t acc = 0;
      for (; index < field.size(); ++index) {
        if (field[index] < '0' || field[index] > '9') {
          return false;
        }
        acc = acc * 10 + static_cast<std::uint64_t>(field[index] - '0');
      }
      value = sign < 0 ? -static_cast<std::int64_t>(acc) : static_cast<std::int64_t>(acc);
      return true;
    };
    std::int64_t numbers[6];
    for (int i = 0; i < 6; ++i) {
      if (!parse_i64(fields[static_cast<std::size_t>(i)], numbers[i])) {
        parsed.error = "number";
        return parsed;
      }
    }
    quad_vision::FlowRow row;
    row.t_us = static_cast<std::uint64_t>(numbers[0]);
    row.dt_us = static_cast<std::uint64_t>(numbers[1]);
    row.dx_px = static_cast<std::int32_t>(numbers[2]);
    row.dy_px = static_cast<std::int32_t>(numbers[3]);
    row.quality = static_cast<std::int32_t>(numbers[4]);
    row.valid = static_cast<std::int32_t>(numbers[5]);
    if (row.valid != 0 && row.valid != 1) {
      parsed.error = "valid flag";
      return parsed;
    }
    if (row.quality < 0 || row.quality > 255) {
      parsed.error = "quality range";
      return parsed;
    }
    if (row.valid == 0 && (row.dx_px != 0 || row.dy_px != 0 || row.quality != 0)) {
      parsed.error = "invalid row must be zero";
      return parsed;
    }
    parsed.rows.push_back(row);
  }
  parsed.ok = true;
  return parsed;
}

void test_sequence_and_cli() {
  const std::filesystem::path dir =
      std::filesystem::temp_directory_path() / "quad-vision-tests" / "sequence";
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir / "sub");

  std::string error;
  const quad_vision::Image first = render_packed(48, 40, 0, 0);
  const quad_vision::Image second = render_packed(48, 40, -4, 2);
  quad_vision::Image third = render_packed(48, 40, 0, 0);
  for (std::uint8_t& pixel : third.pixels) {
    pixel = 90;
  }
  CHECK(quad_vision::write_pgm((dir / "frame_000.pgm").string(), first, error));
  CHECK(quad_vision::write_pgm((dir / "sub" / "frame_001.pgm").string(), second, error));
  CHECK(quad_vision::write_pgm((dir / "frame_002.pgm").string(), third, error));
  write_text(dir / "manifest.csv",
             "t_us,filename\n0,frame_000.pgm\n20000,sub/frame_001.pgm\n50000,frame_002.pgm\n");

  const std::string expected =
      "t_us,dt_us,dx_px,dy_px,quality,valid\n"
      "20000,20000,-4,2,255,1\n"
      "50000,30000,0,0,0,0\n";
  const quad_vision::ProcessResult processed =
      quad_vision::process_sequence((dir / "manifest.csv").string(), (dir / "flow.csv").string());
  CHECK(processed.ok);
  CHECK_EQ(read_text(dir / "flow.csv"), expected);
  const ParsedFlow parsed = parse_flow(expected);
  CHECK(parsed.ok);
  CHECK_EQ(parsed.rows.size(), static_cast<std::size_t>(2));
  CHECK_EQ(parsed.rows[0].dt_us, static_cast<std::uint64_t>(20000));
  CHECK_EQ(parsed.rows[1].dt_us, static_cast<std::uint64_t>(30000));
  CHECK_EQ(parsed.rows[0].dx_px, static_cast<std::int32_t>(-4));
  CHECK_EQ(parsed.rows[0].dy_px, static_cast<std::int32_t>(2));

  std::string stdout_text;
  std::string stderr_text;
  const std::string exe = shell_quote(QUAD_VISION_EXECUTABLE);
  const int code = run_shell(exe + " --output " + shell_quote((dir / "cli.csv").string()) +
                                 " --manifest " + shell_quote((dir / "manifest.csv").string()),
                             stdout_text, stderr_text, dir);
  CHECK_EQ(code, 0);
  CHECK_EQ(read_text(dir / "cli.csv"), expected);
  CHECK(contains(stdout_text, "pairs=2 valid=1 invalid=1"));
  CHECK(stderr_text.empty());

  const int help = run_shell(exe + " --help", stdout_text, stderr_text, dir);
  CHECK_EQ(help, 0);
  CHECK(contains(stdout_text, "quad-vision"));

  const int missing =
      run_shell(exe + " --manifest " + shell_quote((dir / "manifest.csv").string()), stdout_text,
                stderr_text, dir);
  CHECK_EQ(missing, 1);

  const int unknown = run_shell(exe + " --nope", stdout_text, stderr_text, dir);
  CHECK_EQ(unknown, 1);

  write_text(dir / "bad_time.csv", "t_us,filename\n0,frame_000.pgm\n0,frame_000.pgm\n");
  const std::filesystem::path bad_out = dir / "should-not-exist.csv";
  std::filesystem::remove(bad_out);
  const quad_vision::ProcessResult bad_time =
      quad_vision::process_sequence((dir / "bad_time.csv").string(), bad_out.string());
  CHECK(!bad_time.ok);
  CHECK(contains(bad_time.error, "strictly increasing"));
  CHECK(!std::filesystem::exists(bad_out));

  write_text(dir / "escape.csv", "t_us,filename\n0,../secret.pgm\n10,../secret.pgm\n");
  const quad_vision::ProcessResult escaped =
      quad_vision::process_sequence((dir / "escape.csv").string(), bad_out.string());
  CHECK(!escaped.ok);
  CHECK(contains(escaped.error, "parent traversal"));

  write_text(dir / "outside.pgm", read_text(dir / "frame_000.pgm"));
  write_text(dir / "run" / "absolute.csv",
             "t_us,filename\n0," + (dir / "outside.pgm").string() + "\n10," +
                 (dir / "outside.pgm").string() + "\n");
  const quad_vision::ProcessResult absolute = quad_vision::process_sequence(
      (dir / "run" / "absolute.csv").string(), bad_out.string());
  CHECK(!absolute.ok);
  CHECK(contains(absolute.error, "absolute path"));

  write_text(dir / "mismatch.csv",
             "t_us,filename\n0,frame_000.pgm\n10,frame_small.pgm\n");
  const quad_vision::Image small = render_packed(32, 32, 0, 0);
  CHECK(quad_vision::write_pgm((dir / "frame_small.pgm").string(), small, error));
  const quad_vision::ProcessResult mismatch =
      quad_vision::process_sequence((dir / "mismatch.csv").string(), bad_out.string());
  CHECK(!mismatch.ok);
  CHECK(contains(mismatch.error, "dimension mismatch"));

  write_text(dir / "tiny.csv", "t_us,filename\n0,tiny.pgm\n10,tiny_b.pgm\n");
  quad_vision::Image tiny = render_packed(32, 32, 0, 0);
  tiny.width = 16;
  tiny.height = 16;
  tiny.stride = 16;
  tiny.pixels.resize(16 * 16);
  CHECK(quad_vision::write_pgm((dir / "tiny.pgm").string(), tiny, error));
  CHECK(quad_vision::write_pgm((dir / "tiny_b.pgm").string(), tiny, error));
  const quad_vision::ProcessResult tiny_result =
      quad_vision::process_sequence((dir / "tiny.csv").string(), bad_out.string());
  CHECK(!tiny_result.ok);
  CHECK(contains(tiny_result.error, "unsupported frame size"));

  write_text(dir / "broken.csv", "t_us,filename\n0,broken.pgm\n10,broken.pgm\n");
  write_text(dir / "broken.pgm", "P2\n32 32\n255\n0\n");
  const int broken = run_shell(exe + " --manifest " + shell_quote((dir / "broken.csv").string()) +
                                   " --output " + shell_quote(bad_out.string()),
                               stdout_text, stderr_text, dir);
  CHECK_EQ(broken, 1);
  CHECK(contains(stderr_text, "unsupported magic"));
  CHECK(!std::filesystem::exists(bad_out));

  write_text(dir / "missing.csv", "t_us,filename\n0,nope.pgm\n10,nope.pgm\n");
  const quad_vision::ProcessResult missing_file =
      quad_vision::process_sequence((dir / "missing.csv").string(), bad_out.string());
  CHECK(!missing_file.ok);
  CHECK(contains(missing_file.error, "could not open"));
}

void test_flow_formatter_forces_invalid_zeros() {
  quad_vision::FlowRow row;
  row.t_us = 5;
  row.dt_us = 5;
  row.dx_px = 4;
  row.dy_px = -2;
  row.quality = 10;
  row.valid = 0;
  const std::string csv = quad_vision::format_flow_csv({row});
  CHECK_EQ(csv, std::string("t_us,dt_us,dx_px,dy_px,quality,valid\n5,5,0,0,0,0\n"));
}

void write_examples() {
  const std::filesystem::path root{QUAD_VISION_SOURCE_DIR};
  const std::filesystem::path demo = root / "examples" / "translate_demo";
  std::filesystem::create_directories(demo);
  std::string error;
  const quad_vision::Image first = render_packed(64, 64, 0, 0);
  const quad_vision::Image second = render_packed(64, 64, 3, 0);
  quad_vision::Image third = render_packed(64, 64, 0, 0);
  for (std::uint8_t& pixel : third.pixels) {
    pixel = 110;
  }
  CHECK(quad_vision::write_pgm((demo / "frame_000.pgm").string(), first, error));
  CHECK(quad_vision::write_pgm((demo / "frame_001.pgm").string(), second, error));
  CHECK(quad_vision::write_pgm((demo / "frame_002.pgm").string(), third, error));
  write_text(demo / "manifest.csv",
             "t_us,filename\n0,frame_000.pgm\n20000,frame_001.pgm\n50000,frame_002.pgm\n");
  const std::filesystem::path flow = demo / "expected_flow.csv";
  const quad_vision::ProcessResult result = quad_vision::process_sequence(
      (demo / "manifest.csv").string(), flow.string());
  CHECK(result.ok);
  const std::string expected =
      "t_us,dt_us,dx_px,dy_px,quality,valid\n"
      "20000,20000,3,0,255,1\n"
      "50000,30000,0,0,0,0\n";
  CHECK_EQ(read_text(flow), expected);

  write_text(root / "examples" / "unsafe" / "manifest.csv",
             "t_us,filename\n0,../secret.pgm\n20000,../secret.pgm\n");
  const quad_vision::ProcessResult unsafe = quad_vision::process_sequence(
      (root / "examples" / "unsafe" / "manifest.csv").string(),
      (std::filesystem::temp_directory_path() / "quad-vision-tests" / "unsafe-out.csv").string());
  CHECK(!unsafe.ok);
  CHECK(contains(unsafe.error, "parent traversal"));
}

void test_bench_executable() {
  const std::filesystem::path dir =
      std::filesystem::temp_directory_path() / "quad-vision-tests" / "bench";
  std::filesystem::create_directories(dir);
  std::string stdout_text;
  std::string stderr_text;
  const int code = run_shell(shell_quote(QUAD_VISION_BENCH), stdout_text, stderr_text, dir);
  CHECK_EQ(code, 0);
  CHECK(contains(stdout_text, "measurement=host_timing"));
  CHECK(contains(stdout_text, "label=host timing"));
  CHECK(contains(stdout_text, "not_hls_synthesis=1"));
  CHECK(contains(stdout_text, "not_fpga=1"));
  CHECK(stderr_text.empty());
}

}  // namespace

int main() {
  test_kernel_source_is_bounded();
  test_pgm();
  test_manifest_parser();
  test_flow_formatter_forces_invalid_zeros();
  test_shifts();
  test_brightness_and_noise();
  test_rejections();
  test_partial_texture_quality();
  test_stride_and_arguments();
  test_sequence_and_cli();
  write_examples();
  test_bench_executable();
  std::cout << "checks=" << g_checks << " failures=" << g_failures << "\n";
  return g_failures == 0 ? 0 : 1;
}
