#include "quad_vision/kernel.hpp"

#include "pattern.hpp"

#include <chrono>
#include <iostream>
#include <vector>

namespace {

struct Plane {
  int width = 0;
  int height = 0;
  int stride = 0;
  std::vector<uint8_t> pixels;
};

Plane render(int width, int height, int x0, int y0) {
  Plane plane;
  plane.width = width;
  plane.height = height;
  plane.stride = width;
  plane.pixels.resize(static_cast<std::size_t>(width * height));
  for (int y = 0; y < height; ++y) {
    for (int x = 0; x < width; ++x) {
      plane.pixels[static_cast<std::size_t>(y * width + x)] =
          quad_vision_test_sample(x0 + x, y0 + y);
    }
  }
  return plane;
}

bool timed_case(int width, int height, int shift_x, int shift_y, int iterations) {
  // curr(x, y) = sample(x - shift_x, y - shift_y), so content moves by +(shift).
  const Plane prev = render(width, height, 0, 0);
  const Plane curr = render(width, height, -shift_x, -shift_y);
  quad_vision::FlowEstimate estimate{};
  for (int i = 0; i < 3; ++i) {
    if (quad_vision::estimate_kernel(prev.pixels.data(), curr.pixels.data(), width, height,
                                     prev.stride, curr.stride, &estimate) !=
        quad_vision::KernelStatus::Ok) {
      std::cerr << "bench: estimator failed during warmup\n";
      return false;
    }
  }
  const auto start = std::chrono::steady_clock::now();
  for (int i = 0; i < iterations; ++i) {
    if (quad_vision::estimate_kernel(prev.pixels.data(), curr.pixels.data(), width, height,
                                     prev.stride, curr.stride, &estimate) !=
        quad_vision::KernelStatus::Ok) {
      std::cerr << "bench: estimator failed\n";
      return false;
    }
  }
  const auto stop = std::chrono::steady_clock::now();
  const double elapsed_ms =
      std::chrono::duration<double, std::milli>(stop - start).count();
  const double per_pair_us = elapsed_ms * 1000.0 / static_cast<double>(iterations);

  if (estimate.valid != 1 || estimate.dx_px != shift_x || estimate.dy_px != shift_y) {
    std::cerr << "bench: expected displacement " << shift_x << "," << shift_y
              << " but kernel returned dx=" << estimate.dx_px << " dy=" << estimate.dy_px
              << " valid=" << estimate.valid << "\n";
    return false;
  }

  std::cout.setf(std::ios::fixed);
  std::cout.precision(3);
  std::cout << "measurement=host_timing\n"
            << "label=host timing\n"
            << "device=host_cpu\n"
            << "not_fpga=1\n"
            << "not_hls_synthesis=1\n"
            << "iterations=" << iterations << "\n"
            << "width=" << width << "\n"
            << "height=" << height << "\n"
            << "elapsed_ms=" << elapsed_ms << "\n"
            << "per_pair_us=" << per_pair_us << "\n"
            << "dx_px=" << estimate.dx_px << "\n"
            << "dy_px=" << estimate.dy_px << "\n"
            << "quality=" << estimate.quality << "\n"
            << "valid=" << estimate.valid << "\n";
  return true;
}

}  // namespace

int main() {
  std::cout << "quad-vision-bench\n"
            << "note=host timing only; no FPGA board or HLS synthesis is selected\n";
  if (!timed_case(64, 64, 4, -2, 100)) {
    return 1;
  }
  if (!timed_case(128, 128, 4, -2, 20)) {
    return 1;
  }
  return 0;
}
