#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace quadsim {

// Shared-schema limits from docs/interfaces.md.
inline constexpr int kImageWidth = 64;
inline constexpr int kImageHeight = 48;
inline constexpr int kSearchWindowPx = 8;
inline constexpr std::uint64_t kFrameDtUs = 20000;
inline constexpr std::uint64_t kInputTimeoutUs = 250000;
inline constexpr int kMinFrames = 2;
inline constexpr int kMaxFrames = 32;

struct ParseError {
    int line = 0;
    std::string message;
};

struct CommandRow {
    std::uint64_t t_us = 0;
    double forward = 0;
    double right = 0;
    double up = 0;
    double yaw_cw = 0;
    int enabled = 0;
};

struct FlowRow {
    std::uint64_t t_us = 0;
    std::uint64_t dt_us = 0;
    double dx_px = 0;
    double dy_px = 0;
    int quality = 0;
    int valid = 0;
};

struct ManifestRow {
    std::uint64_t t_us = 0;
    std::string filename;
};

struct CommandParse {
    std::vector<CommandRow> rows;
    std::optional<ParseError> error;
    bool ok() const { return !error.has_value(); }
};

struct FlowParse {
    std::vector<FlowRow> rows;
    std::optional<ParseError> error;
    bool ok() const { return !error.has_value(); }
};

struct ManifestParse {
    std::vector<ManifestRow> rows;
    std::optional<ParseError> error;
    bool ok() const { return !error.has_value(); }
};

struct TimingGap {
    std::uint64_t t_us = 0;
    std::uint64_t gap_us = 0;
};

}  // namespace quadsim
