#include "quad_vision/process.hpp"

#include "quad_vision/constants.hpp"
#include "quad_vision/image.hpp"
#include "quad_vision/kernel.hpp"
#include "quad_vision/manifest.hpp"

#include <filesystem>
#include <fstream>

namespace quad_vision {
namespace {

ProcessResult fail(const std::string& error) {
  ProcessResult result;
  result.ok = false;
  result.error = error;
  return result;
}

}  // namespace

ProcessResult process_sequence(const std::string& manifest_path,
                               const std::string& output_path) {
  if (manifest_path.empty() || output_path.empty()) {
    return fail("sequence: missing path");
  }
  const ManifestResult manifest = read_manifest(manifest_path);
  if (!manifest.ok) {
    return fail(manifest.error);
  }

  ProcessResult result;
  Image previous;
  bool have_previous = false;
  std::uint64_t previous_time = 0;

  for (const ManifestFrame& frame : manifest.manifest.frames) {
    const std::filesystem::path image_path =
        std::filesystem::path(manifest.manifest.directory) / frame.filename;
    const ImageResult image = read_pgm(image_path.string());
    if (!image.ok) {
      return fail(image.error + ": " + frame.filename);
    }
    if (image.image.width < kMinDimension || image.image.height < kMinDimension ||
        image.image.width > kMaxDimension || image.image.height > kMaxDimension) {
      return fail("sequence: unsupported frame size " + std::to_string(image.image.width) + "x" +
                  std::to_string(image.image.height) + " in " + frame.filename);
    }
    if (!have_previous) {
      previous = image.image;
      previous_time = frame.t_us;
      have_previous = true;
      continue;
    }
    if (image.image.width != previous.width || image.image.height != previous.height) {
      return fail("sequence: dimension mismatch for " + frame.filename);
    }

    FlowEstimate estimate{};
    const KernelStatus status =
        estimate_kernel(previous.data(), image.image.data(), previous.width, previous.height,
                        previous.stride, image.image.stride, &estimate);
    if (status != KernelStatus::Ok) {
      return fail("sequence: estimator rejected " + frame.filename);
    }
    if (estimate.valid != 0 && estimate.valid != 1) {
      return fail("sequence: estimator returned a bad validity flag");
    }
    if (estimate.quality < 0 || estimate.quality > 255) {
      return fail("sequence: estimator returned a bad quality");
    }

    FlowRow row;
    row.t_us = frame.t_us;
    row.dt_us = frame.t_us - previous_time;
    row.dx_px = estimate.dx_px;
    row.dy_px = estimate.dy_px;
    row.quality = estimate.quality;
    row.valid = estimate.valid;
    if (row.valid == 0) {
      row.dx_px = 0;
      row.dy_px = 0;
      row.quality = 0;
    }
    result.rows.push_back(row);
    previous = image.image;
    previous_time = frame.t_us;
  }

  const std::string csv = format_flow_csv(result.rows);
  const std::filesystem::path destination(output_path);
  std::filesystem::path temporary = destination;
  temporary += ".tmp";
  {
    std::ofstream out(temporary, std::ios::binary | std::ios::trunc);
    if (!out) {
      return fail("output: could not write temporary file");
    }
    out.write(csv.data(), static_cast<std::streamsize>(csv.size()));
    if (!out) {
      std::error_code ignore;
      std::filesystem::remove(temporary, ignore);
      return fail("output: could not write temporary file");
    }
  }
  std::error_code rename_error;
  std::filesystem::rename(temporary, destination, rename_error);
  if (rename_error) {
    std::error_code ignore;
    std::filesystem::remove(temporary, ignore);
    return fail("output: could not replace output file");
  }
  result.ok = true;
  return result;
}

}  // namespace quad_vision
