#include "quadsim/generate.hpp"

#include "quadsim/csv_io.hpp"
#include "quadsim/pgm.hpp"
#include "quadsim/types.hpp"

#include <algorithm>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <sstream>
#include <utility>

namespace quadsim {
namespace {

struct Shift {
    int dx = 0;
    int dy = 0;
};

struct ScenarioInfoStorage {
    const char* name;
    const char* classification;
    const char* summary;
};

const ScenarioInfoStorage kCatalog[] = {
    {"nominal", "valid_nominal",
     "Textured frames, exact shifts inside the search window, and a schema-valid command log."},
    {"translation_cardinal", "valid_nominal",
     "Axis-aligned shifts at the +/-8 px search-window boundary, plus a diagonal and a hold."},
    {"low_texture", "valid_unmeasurable_flow",
     "Constant gray frames. Flow truth is invalid, with zero displacement and quality 0."},
    {"image_noise", "valid_degraded_pixels",
     "The cardinal translation with deterministic pixel noise. Geometric truth stays exact."},
    {"dropped_frame", "valid_incomplete_sequence",
     "One frame is omitted. The spanning flow row uses the summed shift and the summed dt."},
    {"frozen_frames", "valid_physically_ambiguous",
     "Textured frames repeat. Apparent displacement is zero while a scripted shift is suppressed."},
    {"command_stale_gap", "valid_operationally_stale",
     "Schema-valid commands with a gap above the 250 ms input timeout."},
    {"corrupt_suite", "invalid_data",
     "Intentional malformed command, flow, manifest, and PGM fixtures, plus one empty valid flow."},
};

const Shift kCardinal[] = {
    {8, 0}, {0, 8}, {-8, 0}, {0, -8}, {4, -3}, {0, 0},
};

const Shift kNominalCycle[] = {
    {1, 0}, {0, -1}, {2, 1}, {-1, 0}, {0, 2}, {-2, -1}, {3, 0}, {0, -3},
};

struct Bundle {
    std::string classification;
    std::string note;
    int frame_count = 0;
    int noise_amplitude = 0;
    std::uint64_t max_gap_us = 0;
    int stale_gap_count = 0;
    int max_abs_shift_px = 0;
    std::vector<Shift> shifts;
    std::vector<Shift> suppressed;
    std::vector<CommandRow> commands;
    std::vector<FlowRow> flow;
    std::vector<ManifestRow> manifest;
    std::vector<Image> images;
    std::vector<std::pair<std::string, std::string>> extra_files;
    std::vector<std::string> defects;
};

std::uint32_t mix32(std::uint32_t value) {
    value ^= value >> 16;
    value *= 0x7feb352du;
    value ^= value >> 15;
    value *= 0x846ca68bu;
    value ^= value >> 16;
    return value;
}

int floor_div4(int value) {
    if (value >= 0) {
        return value / 4;
    }
    return -(((-value) + 3) / 4);
}

// hash_v1. Negative coordinates use two's-complement uint32 mixing and floor division.
std::uint8_t texture_pixel(int x, int y, std::uint32_t seed) {
    const std::uint32_t mixed = mix32(seed ^ (static_cast<std::uint32_t>(x) * 0x9E3779B1u) ^
                                      (static_cast<std::uint32_t>(y) * 0x85EBCA6Bu));
    const int checker = (floor_div4(x) ^ floor_div4(y)) & 1;
    const int block = checker ? 170 : 50;
    const int jitter = static_cast<int>(mixed % 31u) - 15;
    int value = block + jitter;
    if (value < 0) {
        value = 0;
    }
    if (value > 255) {
        value = 255;
    }
    return static_cast<std::uint8_t>(value);
}

class Rng {
public:
    explicit Rng(std::uint32_t seed) : state_(seed == 0 ? 0x6D2B79F5u : seed) {}

    std::uint32_t next() {
        state_ ^= state_ << 13;
        state_ ^= state_ >> 17;
        state_ ^= state_ << 5;
        if (state_ == 0) {
            state_ = 0xA5A5A5A5u;
        }
        return state_;
    }

private:
    std::uint32_t state_;
};

Image render_texture(int cx, int cy, std::uint32_t seed) {
    Image image;
    image.width = kImageWidth;
    image.height = kImageHeight;
    image.pixels.resize(static_cast<std::size_t>(kImageWidth * kImageHeight));
    for (int y = 0; y < kImageHeight; ++y) {
        for (int x = 0; x < kImageWidth; ++x) {
            image.pixels[static_cast<std::size_t>(y * kImageWidth + x)] =
                texture_pixel(x - cx, y - cy, seed);
        }
    }
    return image;
}

Image render_flat() {
    Image image;
    image.width = kImageWidth;
    image.height = kImageHeight;
    image.pixels.assign(static_cast<std::size_t>(kImageWidth * kImageHeight), 128);
    return image;
}

void apply_noise(Image& image, Rng& rng, int amplitude) {
    const std::uint32_t span = static_cast<std::uint32_t>(2 * amplitude + 1);
    for (auto& pixel : image.pixels) {
        const int delta = static_cast<int>(rng.next() % span) - amplitude;
        int value = static_cast<int>(pixel) + delta;
        if (value < 0) {
            value = 0;
        }
        if (value > 255) {
            value = 255;
        }
        pixel = static_cast<std::uint8_t>(value);
    }
}

std::string frame_name(int index) {
    std::ostringstream out;
    out << "frame_" << std::setw(3) << std::setfill('0') << index << ".pgm";
    return out.str();
}

void require_in_window(const Shift& shift) {
    if (std::abs(shift.dx) > kSearchWindowPx || std::abs(shift.dy) > kSearchWindowPx) {
        throw std::logic_error("scripted shift exceeds the search window");
    }
}

int max_abs_shift(const std::vector<Shift>& shifts) {
    int max_abs = 0;
    for (const auto& shift : shifts) {
        max_abs = std::max(max_abs, std::max(std::abs(shift.dx), std::abs(shift.dy)));
    }
    return max_abs;
}

std::string join_shifts(const std::vector<Shift>& shifts) {
    std::ostringstream out;
    for (std::size_t i = 0; i < shifts.size(); ++i) {
        if (i != 0) {
            out << ',';
        }
        out << shifts[i].dx << ':' << shifts[i].dy;
    }
    return out.str();
}

std::vector<CommandRow> commands_for_times(const std::vector<std::uint64_t>& times) {
    std::vector<CommandRow> rows;
    for (std::size_t i = 0; i < times.size(); ++i) {
        CommandRow row;
        row.t_us = times[i];
        const bool endpoint = i == 0 || i + 1 == times.size();
        if (!endpoint) {
            row.forward = (static_cast<int>(i % 5) - 2) * 0.2;
            row.right = (static_cast<int>(i % 3) - 1) * 0.25;
            row.up = 0.0;
            row.yaw_cw = (i % 2 == 0) ? 0.1 : -0.1;
            row.enabled = 1;
        }
        rows.push_back(row);
    }
    return rows;
}

void annotate_command_gaps(Bundle& bundle) {
    bundle.max_gap_us = 0;
    bundle.stale_gap_count = 0;
    for (std::size_t i = 1; i < bundle.commands.size(); ++i) {
        const std::uint64_t gap = bundle.commands[i].t_us - bundle.commands[i - 1].t_us;
        bundle.max_gap_us = std::max(bundle.max_gap_us, gap);
        if (gap > kInputTimeoutUs) {
            ++bundle.stale_gap_count;
        }
    }
}

std::vector<Shift> nominal_shifts(int frames) {
    std::vector<Shift> shifts;
    const int intervals = frames - 1;
    const int cycle = static_cast<int>(sizeof(kNominalCycle) / sizeof(kNominalCycle[0]));
    for (int i = 0; i < intervals; ++i) {
        shifts.push_back(kNominalCycle[i % cycle]);
        require_in_window(shifts.back());
    }
    return shifts;
}

std::vector<Shift> cardinal_shifts() {
    std::vector<Shift> shifts(std::begin(kCardinal), std::end(kCardinal));
    for (const auto& shift : shifts) {
        require_in_window(shift);
    }
    return shifts;
}

void add_valid_sequence(Bundle& bundle, const std::vector<Shift>& shifts, std::uint32_t seed,
                        int noise_amplitude) {
    bundle.shifts = shifts;
    bundle.max_abs_shift_px = max_abs_shift(shifts);
    bundle.noise_amplitude = noise_amplitude;
    bundle.frame_count = static_cast<int>(shifts.size()) + 1;
    Rng rng(seed);
    int cx = 0;
    int cy = 0;
    std::vector<std::uint64_t> times;
    for (int frame = 0; frame < bundle.frame_count; ++frame) {
        const std::uint64_t t_us = static_cast<std::uint64_t>(frame) * kFrameDtUs;
        times.push_back(t_us);
        Image image = render_texture(cx, cy, seed);
        if (noise_amplitude > 0) {
            apply_noise(image, rng, noise_amplitude);
        }
        bundle.images.push_back(std::move(image));
        bundle.manifest.push_back(ManifestRow{t_us, frame_name(frame)});
        if (frame > 0) {
            const Shift& shift = shifts[static_cast<std::size_t>(frame - 1)];
            FlowRow row;
            row.t_us = t_us;
            row.dt_us = kFrameDtUs;
            row.dx_px = shift.dx;
            row.dy_px = shift.dy;
            row.quality = 255;
            row.valid = 1;
            bundle.flow.push_back(row);
        }
        if (frame < static_cast<int>(shifts.size())) {
            cx += shifts[static_cast<std::size_t>(frame)].dx;
            cy += shifts[static_cast<std::size_t>(frame)].dy;
        }
    }
    bundle.commands = commands_for_times(times);
    annotate_command_gaps(bundle);
}

Bundle make_nominal(int frames, std::uint32_t seed) {
    Bundle bundle;
    bundle.classification = "valid_nominal";
    bundle.note =
        "Apparent image displacement is in pixels. Command axes are normalized stick positions "
        "in [-1,1] and are not a flight-dynamics model.";
    add_valid_sequence(bundle, nominal_shifts(frames), seed, 0);
    return bundle;
}

Bundle make_cardinal(std::uint32_t seed, int noise_amplitude, const std::string& classification,
                     const std::string& note) {
    Bundle bundle;
    bundle.classification = classification;
    bundle.note = note;
    add_valid_sequence(bundle, cardinal_shifts(), seed, noise_amplitude);
    return bundle;
}

Bundle make_low_texture(std::uint32_t seed) {
    (void)seed;
    Bundle bundle;
    bundle.classification = "valid_unmeasurable_flow";
    bundle.frame_count = 4;
    bundle.note =
        "Frames are constant gray. Empty texture produces invalid flow. A zero-motion valid "
        "result would claim a confident stationary measurement.";
    std::vector<std::uint64_t> times;
    for (int frame = 0; frame < bundle.frame_count; ++frame) {
        const std::uint64_t t_us = static_cast<std::uint64_t>(frame) * kFrameDtUs;
        times.push_back(t_us);
        bundle.images.push_back(render_flat());
        bundle.manifest.push_back(ManifestRow{t_us, frame_name(frame)});
        if (frame > 0) {
            FlowRow row;
            row.t_us = t_us;
            row.dt_us = kFrameDtUs;
            row.valid = 0;
            bundle.flow.push_back(row);
        }
    }
    bundle.commands = commands_for_times(times);
    annotate_command_gaps(bundle);
    return bundle;
}

Bundle make_dropped(std::uint32_t seed) {
    Bundle bundle;
    bundle.classification = "valid_incomplete_sequence";
    bundle.note =
        "Frame time 40000 is omitted. Flow from 20000 to 60000 sums the hidden interval (0,3) "
        "and the following interval (1,-1). Commands keep the original 20000 us timeline.";
    const Shift intervals[] = {{2, 0}, {0, 3}, {1, -1}, {-4, 0}, {0, 2}};
    for (const auto& shift : intervals) {
        require_in_window(shift);
    }
    constexpr int kFrames = 6;
    constexpr int kDropIndex = 2;
    int cx = 0;
    int cy = 0;
    int emitted = 0;
    std::uint64_t previous_emitted_t = 0;
    bool have_emitted = false;
    for (int frame = 0; frame < kFrames; ++frame) {
        const std::uint64_t t_us = static_cast<std::uint64_t>(frame) * kFrameDtUs;
        if (frame != kDropIndex) {
            bundle.images.push_back(render_texture(cx, cy, seed));
            bundle.manifest.push_back(ManifestRow{t_us, frame_name(emitted)});
            if (have_emitted) {
                const bool spans_drop = previous_emitted_t < 40000 && t_us > 40000;
                FlowRow row;
                row.t_us = t_us;
                row.dt_us = t_us - previous_emitted_t;
                if (spans_drop) {
                    row.dx_px = intervals[1].dx + intervals[2].dx;
                    row.dy_px = intervals[1].dy + intervals[2].dy;
                } else {
                    row.dx_px = intervals[frame - 1].dx;
                    row.dy_px = intervals[frame - 1].dy;
                }
                row.quality = 255;
                row.valid = 1;
                bundle.flow.push_back(row);
                bundle.shifts.push_back(
                    Shift{static_cast<int>(row.dx_px), static_cast<int>(row.dy_px)});
            }
            previous_emitted_t = t_us;
            have_emitted = true;
            ++emitted;
        }
        if (frame < kFrames - 1) {
            cx += intervals[frame].dx;
            cy += intervals[frame].dy;
        }
    }
    bundle.frame_count = emitted;
    bundle.max_abs_shift_px = max_abs_shift(bundle.shifts);
    std::vector<std::uint64_t> command_times;
    for (int frame = 0; frame < kFrames; ++frame) {
        command_times.push_back(static_cast<std::uint64_t>(frame) * kFrameDtUs);
    }
    bundle.commands = commands_for_times(command_times);
    annotate_command_gaps(bundle);
    return bundle;
}

Bundle make_frozen(std::uint32_t seed) {
    Bundle bundle;
    bundle.classification = "valid_physically_ambiguous";
    bundle.note =
        "Frozen rows copy the previous textured frame. Truth dx and dy are the apparent "
        "image-content displacement, which is zero. suppressed_shifts records the scripted "
        "motion that the repeated frame does not show. Zero displacement here is not a hover proof.";
    const Shift planned[] = {{3, -1}, {2, 0}, {0, -2}, {0, 2}};
    const bool frozen[] = {false, true, true, false};
    for (const auto& shift : planned) {
        require_in_window(shift);
    }
    int cx = 0;
    int cy = 0;
    Image previous;
    for (int frame = 0; frame < 5; ++frame) {
        const std::uint64_t t_us = static_cast<std::uint64_t>(frame) * kFrameDtUs;
        const bool is_frozen = frame > 0 && frozen[frame - 1];
        Image image;
        if (is_frozen) {
            image = previous;
            bundle.suppressed.push_back(planned[frame - 1]);
            bundle.shifts.push_back(Shift{0, 0});
            FlowRow row;
            row.t_us = t_us;
            row.dt_us = kFrameDtUs;
            row.quality = 255;
            row.valid = 1;
            bundle.flow.push_back(row);
        } else {
            if (frame > 0) {
                cx += planned[frame - 1].dx;
                cy += planned[frame - 1].dy;
                bundle.shifts.push_back(planned[frame - 1]);
                FlowRow row;
                row.t_us = t_us;
                row.dt_us = kFrameDtUs;
                row.dx_px = planned[frame - 1].dx;
                row.dy_px = planned[frame - 1].dy;
                row.quality = 255;
                row.valid = 1;
                bundle.flow.push_back(row);
            }
            image = render_texture(cx, cy, seed);
        }
        bundle.images.push_back(image);
        bundle.manifest.push_back(ManifestRow{t_us, frame_name(frame)});
        previous = std::move(image);
    }
    bundle.frame_count = 5;
    bundle.max_abs_shift_px = max_abs_shift(bundle.shifts);
    std::vector<std::uint64_t> times;
    for (int frame = 0; frame < 5; ++frame) {
        times.push_back(static_cast<std::uint64_t>(frame) * kFrameDtUs);
    }
    bundle.commands = commands_for_times(times);
    annotate_command_gaps(bundle);
    return bundle;
}

Bundle make_stale() {
    Bundle bundle;
    bundle.classification = "valid_operationally_stale";
    bundle.note =
        "Timestamps stay strictly increasing and axes stay inside [-1,1]. The 680000 us gap "
        "exceeds the 250000 us ground-station input timeout. This file is schema-valid. "
        "It does not command a landing.";
    bundle.commands = {
        CommandRow{0, 0, 0, 0, 0, 0},
        CommandRow{20000, 0.2, 0, 0, 0, 1},
        CommandRow{700000, 0.2, 0, 0, 0, 1},
        CommandRow{720000, 0, 0, 0, 0, 0},
    };
    annotate_command_gaps(bundle);
    return bundle;
}

Bundle make_corrupt(std::uint32_t seed) {
    Bundle bundle;
    bundle.classification = "invalid_data";
    bundle.note =
        "Every defect file is schema-invalid except flow_header_only.csv, which is a valid "
        "empty flow stream. Empty agreement is not a displacement success.";
    const std::vector<std::pair<std::string, std::string>> files = {
        {"commands_out_of_range.csv",
         "t_us,forward,right,up,yaw_cw,enabled\n0,0,0,0,0,0\n20000,0,1.5,0,0,1\n"},
        {"commands_duplicate_timestamp.csv",
         "t_us,forward,right,up,yaw_cw,enabled\n0,0,0,0,0,0\n20000,0.1,0,0,0,1\n20000,0.2,0,0,0,1\n"},
        {"commands_nonmonotonic.csv",
         "t_us,forward,right,up,yaw_cw,enabled\n0,0,0,0,0,0\n40000,0.1,0,0,0,1\n20000,0.1,0,0,0,1\n"},
        {"commands_bad_enabled.csv",
         "t_us,forward,right,up,yaw_cw,enabled\n0,0,0,0,0,0\n20000,0.1,0,0,0,2\n"},
        {"commands_nonfinite.csv",
         "t_us,forward,right,up,yaw_cw,enabled\n0,0,0,0,0,0\n20000,nan,0,0,0,1\n"},
        {"commands_infinity.csv",
         "t_us,forward,right,up,yaw_cw,enabled\n0,0,0,0,0,0\n20000,inf,0,0,0,1\n"},
        {"commands_negative_time.csv",
         "t_us,forward,right,up,yaw_cw,enabled\n-1,0,0,0,0,0\n"},
        {"commands_quoted.csv",
         "t_us,forward,right,up,yaw_cw,enabled\n0,0,0,0,0,0\n\"20000\",0,0,0,0,1\n"},
        {"commands_crlf.csv",
         "t_us,forward,right,up,yaw_cw,enabled\r\n0,0,0,0,0,0\r\n"},
        {"commands_bad_header.csv", "time,forward,right,up,yaw,enabled\n0,0,0,0,0,0\n"},
        {"flow_duplicate_timestamp.csv",
         "t_us,dt_us,dx_px,dy_px,quality,valid\n20000,20000,1,0,255,1\n20000,20000,1,0,255,1\n"},
        {"flow_invalid_nonzero.csv",
         "t_us,dt_us,dx_px,dy_px,quality,valid\n20000,20000,2,0,0,0\n"},
        {"flow_invalid_quality.csv",
         "t_us,dt_us,dx_px,dy_px,quality,valid\n20000,20000,0,0,10,0\n"},
        {"flow_bad_quality.csv",
         "t_us,dt_us,dx_px,dy_px,quality,valid\n20000,20000,1,0,300,1\n"},
        {"flow_unordered.csv",
         "t_us,dt_us,dx_px,dy_px,quality,valid\n40000,20000,1,0,255,1\n20000,20000,0,1,255,1\n"},
        {"flow_zero_dt.csv", "t_us,dt_us,dx_px,dy_px,quality,valid\n20000,0,1,0,255,1\n"},
        {"flow_dt_mismatch.csv",
         "t_us,dt_us,dx_px,dy_px,quality,valid\n20000,20000,1,0,255,1\n50000,20000,1,0,255,1\n"},
        {"flow_header_only.csv", "t_us,dt_us,dx_px,dy_px,quality,valid\n"},
        {"manifest_parent_traversal.csv", "t_us,filename\n0,../frame_000.pgm\n"},
        {"manifest_absolute.csv", "t_us,filename\n0,/tmp/frame_000.pgm\n"},
        {"manifest_duplicate_time.csv",
         "t_us,filename\n0,frame_000.pgm\n0,frame_001.pgm\n"},
        {"empty.csv", ""},
    };
    for (const auto& file : files) {
        bundle.extra_files.push_back(file);
        const bool valid_empty = file.first == "flow_header_only.csv";
        bundle.defects.push_back(std::string(valid_empty ? "valid_empty " : "invalid ") +
                                  file.first);
    }
    std::string truncated = "P5\n# quad-sim truncated seed=" + std::to_string(seed) + "\n8 8\n255\n";
    truncated.append("\x01\x02\x03", 3);
    bundle.extra_files.emplace_back("frame_truncated.pgm", truncated);
    bundle.defects.emplace_back("invalid frame_truncated.pgm");
    return bundle;
}

std::string build_report(const GenerateRequest& request, const Bundle& bundle,
                         const std::vector<std::string>& filenames) {
    std::ostringstream files;
    for (std::size_t i = 0; i < filenames.size(); ++i) {
        if (i != 0) {
            files << ',';
        }
        files << filenames[i];
    }
    std::ostringstream out;
    out << "schema_version=1\n";
    out << "generator=quad-sim\n";
    out << "scenario=" << request.scenario << '\n';
    out << "classification=" << bundle.classification << '\n';
    out << "seed=" << request.seed << '\n';
    out << "search_window_px=" << kSearchWindowPx << '\n';
    out << "image_width=" << kImageWidth << '\n';
    out << "image_height=" << kImageHeight << '\n';
    out << "dt_us=" << kFrameDtUs << '\n';
    out << "frame_count=" << bundle.frame_count << '\n';
    out << "noise_amplitude=" << bundle.noise_amplitude << '\n';
    out << "texture=hash_v1\n";
    out << "rng=xorshift32\n";
    out << "groundstation_timeout_us=" << kInputTimeoutUs << '\n';
    out << "max_gap_us=" << bundle.max_gap_us << '\n';
    out << "stale_gap_count=" << bundle.stale_gap_count << '\n';
    out << "max_abs_shift_px=" << bundle.max_abs_shift_px << '\n';
    out << "shifts=" << join_shifts(bundle.shifts) << '\n';
    out << "suppressed_shifts=" << join_shifts(bundle.suppressed) << '\n';
    out << "files=" << files.str() << '\n';
    for (const auto& defect : bundle.defects) {
        out << "defect=" << defect << '\n';
    }
    out << "note=" << bundle.note << '\n';
    return out.str();
}

void prepare_directory(const std::filesystem::path& directory, bool overwrite) {
    if (directory.empty()) {
        throw std::runtime_error("output directory is empty");
    }
    const auto status = std::filesystem::symlink_status(directory);
    if (std::filesystem::exists(status)) {
        if (std::filesystem::is_symlink(status)) {
            throw std::runtime_error("output path is a symlink");
        }
        if (!std::filesystem::is_directory(status)) {
            throw std::runtime_error("output path exists and is not a directory");
        }
        const bool empty =
            std::filesystem::directory_iterator(directory) == std::filesystem::directory_iterator();
        if (!empty && !overwrite) {
            throw OverwriteRefused("overwrite refused: output directory is not empty");
        }
    } else {
        std::filesystem::create_directories(directory);
    }
}

void write_text(const std::filesystem::path& path, const std::string& text) {
    std::ofstream out(path, std::ios::binary);
    if (!out) {
        throw std::runtime_error("failed to write " + path.string());
    }
    out << text;
    if (!out) {
        throw std::runtime_error("failed to write " + path.string());
    }
}

Bundle build_bundle(const GenerateRequest& request) {
    if (request.frames.has_value() &&
        (*request.frames < kMinFrames || *request.frames > kMaxFrames)) {
        throw std::runtime_error("--frames must be in [2,32]");
    }
    const bool frames_given = request.frames.has_value();
    if (frames_given && request.scenario != "nominal") {
        throw std::runtime_error("--frames is supported only for the nominal scenario");
    }
    const int frames = request.frames.value_or(6);
    if (request.scenario == "nominal") {
        return make_nominal(frames, request.seed);
    }
    if (request.scenario == "translation_cardinal") {
        return make_cardinal(
            request.seed, 0, "valid_nominal",
            "Shifts are exact integer image-content displacements inside the +/-8 px window. "
            "Positive dx is image right and positive dy is image down.");
    }
    if (request.scenario == "low_texture") {
        return make_low_texture(request.seed);
    }
    if (request.scenario == "image_noise") {
        return make_cardinal(
            request.seed, 12, "valid_degraded_pixels",
            "Noise amplitude is 12 gray levels from a deterministic xorshift32 stream. "
            "flow.csv still records the known integer translation at quality 255.");
    }
    if (request.scenario == "dropped_frame") {
        return make_dropped(request.seed);
    }
    if (request.scenario == "frozen_frames") {
        return make_frozen(request.seed);
    }
    if (request.scenario == "command_stale_gap") {
        return make_stale();
    }
    if (request.scenario == "corrupt_suite") {
        return make_corrupt(request.seed);
    }
    throw std::runtime_error("unknown scenario: " + request.scenario);
}

}  // namespace

const ScenarioInfo* scenario_catalog(std::size_t& count) {
    static ScenarioInfo catalog[sizeof(kCatalog) / sizeof(kCatalog[0])];
    static bool ready = false;
    if (!ready) {
        for (std::size_t i = 0; i < sizeof(kCatalog) / sizeof(kCatalog[0]); ++i) {
            catalog[i] = ScenarioInfo{kCatalog[i].name, kCatalog[i].classification,
                                      kCatalog[i].summary};
        }
        ready = true;
    }
    count = sizeof(kCatalog) / sizeof(kCatalog[0]);
    return catalog;
}

GenerateResult generate(const GenerateRequest& request) {
    prepare_directory(request.output_dir, request.overwrite);
    Bundle bundle = build_bundle(request);
    std::vector<std::string> filenames;
    auto remember = [&](const std::string& name) { filenames.push_back(name); };

    if (!bundle.commands.empty()) {
        write_text(request.output_dir / "commands.csv", format_commands(bundle.commands));
        remember("commands.csv");
    }
    if (!bundle.manifest.empty()) {
        write_text(request.output_dir / "manifest.csv", format_manifest(bundle.manifest));
        remember("manifest.csv");
    }
    if (!bundle.flow.empty() || request.scenario == "low_texture") {
        write_text(request.output_dir / "flow.csv", format_flow(bundle.flow));
        remember("flow.csv");
    }
    const std::string comment =
        "quad-sim scenario=" + request.scenario + " seed=" + std::to_string(request.seed);
    for (std::size_t i = 0; i < bundle.images.size(); ++i) {
        const std::string name = frame_name(static_cast<int>(i));
        write_pgm((request.output_dir / name).string(), bundle.images[i], comment);
        remember(name);
    }
    for (const auto& extra : bundle.extra_files) {
        write_text(request.output_dir / extra.first, extra.second);
        remember(extra.first);
    }

    remember("scenario_report.txt");
    std::sort(filenames.begin(), filenames.end());
    const std::string report = build_report(request, bundle, filenames);
    write_text(request.output_dir / "scenario_report.txt", report);

    GenerateResult result;
    result.scenario = request.scenario;
    result.classification = bundle.classification;
    result.output_dir = request.output_dir;
    result.report_text = report;
    for (const auto& name : filenames) {
        result.files.push_back(request.output_dir / name);
    }
    return result;
}

}  // namespace quadsim
