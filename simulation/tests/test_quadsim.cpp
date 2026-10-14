#include "quadsim/csv_io.hpp"
#include "quadsim/evaluate.hpp"
#include "quadsim/generate.hpp"
#include "quadsim/pgm.hpp"
#include "quadsim/types.hpp"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <vector>

#include <sys/wait.h>

namespace {

int g_failed = 0;
int g_passed = 0;
std::string g_cli;
int g_temp_count = 0;

void expect(bool condition, const char* text, int line) {
    if (!condition) {
        std::cerr << "FAIL " << line << ": " << text << '\n';
        ++g_failed;
    } else {
        ++g_passed;
    }
}

#define EXPECT(cond) expect(static_cast<bool>(cond), #cond, __LINE__)

std::string read_text(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw std::runtime_error("failed to read " + path.string());
    }
    std::ostringstream buffer;
    buffer << input.rdbuf();
    return buffer.str();
}

std::string report_value(const std::string& report, const std::string& key) {
    const std::string prefix = key + "=";
    std::istringstream input(report);
    std::string line;
    while (std::getline(input, line)) {
        if (line.rfind(prefix, 0) == 0) {
            return line.substr(prefix.size());
        }
    }
    return "";
}

std::string quote(const std::string& text) {
    std::string out = "'";
    for (char c : text) {
        if (c == '\'') {
            out += "'\\''";
        } else {
            out.push_back(c);
        }
    }
    out.push_back('\'');
    return out;
}

std::string capture(const std::string& command, int& exit_code) {
    FILE* pipe = popen((command + " 2>/dev/null").c_str(), "r");
    if (pipe == nullptr) {
        exit_code = -1;
        return {};
    }
    std::string output;
    char buffer[1024];
    while (std::fgets(buffer, sizeof(buffer), pipe) != nullptr) {
        output += buffer;
    }
    const int status = pclose(pipe);
    exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
    return output;
}

struct TempDir {
    std::filesystem::path path;
    explicit TempDir(const std::string& name) {
        path = std::filesystem::temp_directory_path() /
               ("quadsim-" + std::to_string(static_cast<long long>(::getpid())) + "-" +
                std::to_string(++g_temp_count) + "-" + name);
        std::filesystem::create_directories(path);
    }
    ~TempDir() {
        std::error_code error;
        std::filesystem::remove_all(path, error);
    }
    TempDir(const TempDir&) = delete;
    TempDir& operator=(const TempDir&) = delete;
};

quadsim::GenerateResult generate_scenario(const std::filesystem::path& dir, const std::string& scenario,
                                          std::uint32_t seed, bool overwrite = false,
                                          int frames = -1) {
    quadsim::GenerateRequest request;
    request.scenario = scenario;
    request.output_dir = dir;
    request.seed = seed;
    request.overwrite = overwrite;
    if (frames >= 0) {
        request.frames = frames;
    }
    return quadsim::generate(request);
}

void expect_translation(const quadsim::Image& previous, const quadsim::Image& current, int dx, int dy) {
    EXPECT(previous.width == current.width);
    EXPECT(previous.height == current.height);
    int checked = 0;
    int mismatches = 0;
    for (int y = 0; y < current.height; ++y) {
        for (int x = 0; x < current.width; ++x) {
            const int source_x = x - dx;
            const int source_y = y - dy;
            if (source_x < 0 || source_y < 0 || source_x >= previous.width ||
                source_y >= previous.height) {
                continue;
            }
            const auto actual = current.pixels[static_cast<std::size_t>(y * current.width + x)];
            const auto expected_pixel =
                previous.pixels[static_cast<std::size_t>(source_y * previous.width + source_x)];
            if (actual != expected_pixel) {
                ++mismatches;
            }
            ++checked;
        }
    }
    EXPECT(checked > 500);
    EXPECT(mismatches == 0);
}

quadsim::FlowRow flow_row(std::uint64_t t, std::uint64_t dt, double dx, double dy, int quality,
                          int valid) {
    quadsim::FlowRow row;
    row.t_us = t;
    row.dt_us = dt;
    row.dx_px = dx;
    row.dy_px = dy;
    row.quality = quality;
    row.valid = valid;
    return row;
}

void test_catalog() {
    std::size_t count = 0;
    const quadsim::ScenarioInfo* catalog = quadsim::scenario_catalog(count);
    EXPECT(count == 8);
    bool saw_cardinal = false;
    bool saw_corrupt = false;
    for (std::size_t i = 0; i < count; ++i) {
        if (std::string(catalog[i].name) == "translation_cardinal") {
            saw_cardinal = true;
            EXPECT(std::string(catalog[i].classification) == "valid_nominal");
        }
        if (std::string(catalog[i].name) == "corrupt_suite") {
            saw_corrupt = true;
            EXPECT(std::string(catalog[i].classification) == "invalid_data");
        }
    }
    EXPECT(saw_cardinal);
    EXPECT(saw_corrupt);
}

void test_determinism() {
    TempDir first("det-a");
    TempDir second("det-b");
    TempDir third("det-c");
    const auto a = generate_scenario(first.path, "nominal", 11);
    const auto b = generate_scenario(second.path, "nominal", 11);
    const auto c = generate_scenario(third.path, "nominal", 12);
    EXPECT(a.files.size() == b.files.size());
    for (const auto& file : a.files) {
        const auto name = file.filename();
        EXPECT(read_text(first.path / name) == read_text(second.path / name));
    }
    const auto raster_a = quadsim::read_pgm((first.path / "frame_000.pgm").string());
    const auto raster_c = quadsim::read_pgm((third.path / "frame_000.pgm").string());
    EXPECT(raster_a.pixels != raster_c.pixels);
    EXPECT(report_value(a.report_text, "seed") == "11");
    EXPECT(report_value(a.report_text, "texture") == "hash_v1");
    EXPECT(report_value(a.report_text, "rng") == "xorshift32");
}

void test_cardinal_translation() {
    TempDir dir("cardinal");
    const auto result = generate_scenario(dir.path, "translation_cardinal", 4);
    EXPECT(result.classification == "valid_nominal");
    EXPECT(report_value(result.report_text, "shifts") == "8:0,0:8,-8:0,0:-8,4:-3,0:0");
    EXPECT(report_value(result.report_text, "max_abs_shift_px") == "8");
    EXPECT(report_value(result.report_text, "search_window_px") == "8");
    const auto flow = quadsim::parse_flow(read_text(dir.path / "flow.csv"));
    EXPECT(flow.ok());
    const int expected_dx[] = {8, 0, -8, 0, 4, 0};
    const int expected_dy[] = {0, 8, 0, -8, -3, 0};
    EXPECT(flow.rows.size() == 6);
    std::vector<quadsim::Image> frames;
    for (int i = 0; i < 7; ++i) {
        std::ostringstream name;
        name << "frame_" << (i < 10 ? "00" : "0") << i << ".pgm";
        frames.push_back(quadsim::read_pgm((dir.path / name.str()).string()));
        EXPECT(frames.back().width == 64);
        EXPECT(frames.back().height == 48);
    }
    for (std::size_t i = 0; i < flow.rows.size(); ++i) {
        EXPECT(flow.rows[i].t_us == (i + 1) * 20000);
        EXPECT(flow.rows[i].dt_us == 20000);
        EXPECT(flow.rows[i].dx_px == expected_dx[i]);
        EXPECT(flow.rows[i].dy_px == expected_dy[i]);
        EXPECT(flow.rows[i].quality == 255);
        EXPECT(flow.rows[i].valid == 1);
        EXPECT(std::abs(flow.rows[i].dx_px) <= 8);
        EXPECT(std::abs(flow.rows[i].dy_px) <= 8);
        expect_translation(frames[i], frames[i + 1], expected_dx[i], expected_dy[i]);
        if (i > 0) {
            EXPECT(flow.rows[i].t_us - flow.rows[i - 1].t_us == flow.rows[i].dt_us);
        }
    }
    const std::string header = read_text(dir.path / "frame_000.pgm").substr(0, 2);
    EXPECT(header == "P5");
    EXPECT(read_text(dir.path / "frame_000.pgm").find("255\n") != std::string::npos);
}

void test_nominal_schema() {
    TempDir dir("nominal");
    const auto result = generate_scenario(dir.path, "nominal", 2);
    const auto commands = quadsim::parse_commands(read_text(dir.path / "commands.csv"));
    const auto manifest = quadsim::parse_manifest(read_text(dir.path / "manifest.csv"));
    const auto flow = quadsim::parse_flow(read_text(dir.path / "flow.csv"));
    EXPECT(commands.ok());
    EXPECT(manifest.ok());
    EXPECT(flow.ok());
    EXPECT(commands.rows.size() == 6);
    EXPECT(manifest.rows.size() == 6);
    EXPECT(flow.rows.size() == 5);
    EXPECT(commands.rows.front().enabled == 0);
    EXPECT(commands.rows.back().enabled == 0);
    EXPECT(commands.rows.front().forward == 0);
    for (std::size_t i = 0; i < commands.rows.size(); ++i) {
        EXPECT(commands.rows[i].t_us == i * 20000);
        EXPECT(commands.rows[i].forward >= -1 && commands.rows[i].forward <= 1);
        EXPECT(commands.rows[i].right >= -1 && commands.rows[i].right <= 1);
        EXPECT(commands.rows[i].up >= -1 && commands.rows[i].up <= 1);
        EXPECT(commands.rows[i].yaw_cw >= -1 && commands.rows[i].yaw_cw <= 1);
        EXPECT(commands.rows[i].enabled == 0 || commands.rows[i].enabled == 1);
        EXPECT(manifest.rows[i].filename == "frame_00" + std::to_string(i) + ".pgm");
        EXPECT(!quadsim::manifest_filename_unsafe(manifest.rows[i].filename));
        if (i > 0 && i + 1 != commands.rows.size()) {
            EXPECT(commands.rows[i].enabled == 1);
        }
    }
    EXPECT(quadsim::command_timeout_gaps(commands.rows, quadsim::kInputTimeoutUs).empty());
    EXPECT(report_value(result.report_text, "max_gap_us") == "20000");
    EXPECT(report_value(result.report_text, "stale_gap_count") == "0");

    const auto disabled = quadsim::parse_commands(
        "t_us,forward,right,up,yaw_cw,enabled\n0,0.2,-0.25,0,0.1,0\n20000,0,0,0,0,1\n");
    EXPECT(disabled.ok());
    EXPECT(disabled.rows.front().enabled == 0);
    EXPECT(std::fabs(disabled.rows.front().forward - 0.2) < 1e-9);
}

void test_low_texture() {
    TempDir dir("flat");
    const auto result = generate_scenario(dir.path, "low_texture", 9);
    EXPECT(result.classification == "valid_unmeasurable_flow");
    const auto flow = quadsim::parse_flow(read_text(dir.path / "flow.csv"));
    EXPECT(flow.ok());
    EXPECT(flow.rows.size() == 3);
    for (int i = 0; i < 4; ++i) {
        const auto image =
            quadsim::read_pgm((dir.path / ("frame_00" + std::to_string(i) + ".pgm")).string());
        EXPECT(image.pixels.size() == 64u * 48u);
        int non_flat = 0;
        for (auto pixel : image.pixels) {
            if (pixel != 128) {
                ++non_flat;
            }
        }
        EXPECT(non_flat == 0);
    }
    for (const auto& row : flow.rows) {
        EXPECT(row.valid == 0);
        EXPECT(row.dx_px == 0);
        EXPECT(row.dy_px == 0);
        EXPECT(row.quality == 0);
    }
    const auto verdict = quadsim::evaluate_flow(flow.rows, flow.rows, 0);
    EXPECT(verdict.inputs_well_formed);
    EXPECT(verdict.comparable_samples == 0);
    EXPECT(!verdict.displacement_available);
    EXPECT(verdict.rejection_success == 1);
    EXPECT(verdict.overall_success);
    const std::string text = quadsim::format_evaluation(verdict);
    EXPECT(text.find("mean_displacement_error_px=unavailable") != std::string::npos);
    EXPECT(text.find("mean_displacement_error_px=0") == std::string::npos);
    EXPECT(text.find("agreement on invalid truth") != std::string::npos);

    auto claimed_still = flow.rows;
    for (auto& row : claimed_still) {
        row.valid = 1;
        row.quality = 255;
    }
    const auto false_still = quadsim::evaluate_flow(flow.rows, claimed_still, 0);
    EXPECT(false_still.false_valid_count == 3);
    EXPECT(false_still.comparable_samples == 0);
    EXPECT(!false_still.displacement_available);
    EXPECT(!false_still.overall_success);
}

void test_noise_and_dropped_and_frozen() {
    TempDir clean_dir("clean");
    TempDir noisy_dir("noisy");
    TempDir noisy_again("noisy2");
    generate_scenario(clean_dir.path, "translation_cardinal", 5);
    generate_scenario(noisy_dir.path, "image_noise", 5);
    const auto noisy_repeat = generate_scenario(noisy_again.path, "image_noise", 5);
    EXPECT(read_text(clean_dir.path / "flow.csv") == read_text(noisy_dir.path / "flow.csv"));
    EXPECT(read_text(noisy_dir.path / "frame_001.pgm") == read_text(noisy_again.path / "frame_001.pgm"));
    const auto clean = quadsim::read_pgm((clean_dir.path / "frame_001.pgm").string());
    const auto noisy = quadsim::read_pgm((noisy_dir.path / "frame_001.pgm").string());
    EXPECT(clean.pixels != noisy.pixels);
    EXPECT(noisy_repeat.classification == "valid_degraded_pixels");
    EXPECT(report_value(noisy_repeat.report_text, "noise_amplitude") == "12");

    TempDir dropped_dir("dropped");
    const auto dropped = generate_scenario(dropped_dir.path, "dropped_frame", 6);
    EXPECT(dropped.classification == "valid_incomplete_sequence");
    const auto manifest = quadsim::parse_manifest(read_text(dropped_dir.path / "manifest.csv"));
    EXPECT(manifest.ok());
    EXPECT(manifest.rows.size() == 5);
    EXPECT(manifest.rows[2].t_us == 60000);
    bool saw_dropped_time = false;
    for (const auto& row : manifest.rows) {
        saw_dropped_time = saw_dropped_time || row.t_us == 40000;
        EXPECT(!std::filesystem::path(row.filename).is_absolute());
    }
    EXPECT(!saw_dropped_time);
    EXPECT(!std::filesystem::exists(dropped_dir.path / "frame_005.pgm"));
    const auto dropped_flow = quadsim::parse_flow(read_text(dropped_dir.path / "flow.csv"));
    EXPECT(dropped_flow.ok());
    EXPECT(dropped_flow.rows.size() == 4);
    EXPECT(dropped_flow.rows[0].t_us == 20000 && dropped_flow.rows[0].dt_us == 20000);
    EXPECT(dropped_flow.rows[0].dx_px == 2 && dropped_flow.rows[0].dy_px == 0);
    EXPECT(dropped_flow.rows[1].t_us == 60000 && dropped_flow.rows[1].dt_us == 40000);
    EXPECT(dropped_flow.rows[1].dx_px == 1 && dropped_flow.rows[1].dy_px == 2);
    EXPECT(dropped_flow.rows[2].dx_px == -4 && dropped_flow.rows[2].dy_px == 0);
    EXPECT(dropped_flow.rows[3].dx_px == 0 && dropped_flow.rows[3].dy_px == 2);
    std::vector<quadsim::Image> frames;
    for (const auto& row : manifest.rows) {
        frames.push_back(quadsim::read_pgm((dropped_dir.path / row.filename).string()));
    }
    expect_translation(frames[0], frames[1], 2, 0);
    expect_translation(frames[1], frames[2], 1, 2);
    expect_translation(frames[2], frames[3], -4, 0);
    expect_translation(frames[3], frames[4], 0, 2);
    const auto commands = quadsim::parse_commands(read_text(dropped_dir.path / "commands.csv"));
    EXPECT(commands.ok());
    EXPECT(commands.rows.size() == 6);
    EXPECT(commands.rows[2].t_us == 40000);

    TempDir frozen_dir("frozen");
    const auto frozen = generate_scenario(frozen_dir.path, "frozen_frames", 8);
    EXPECT(frozen.classification == "valid_physically_ambiguous");
    EXPECT(report_value(frozen.report_text, "suppressed_shifts") == "2:0,0:-2");
    const auto frozen_flow = quadsim::parse_flow(read_text(frozen_dir.path / "flow.csv"));
    EXPECT(frozen_flow.ok());
    EXPECT(frozen_flow.rows.size() == 4);
    EXPECT(frozen_flow.rows[0].dx_px == 3 && frozen_flow.rows[0].dy_px == -1);
    EXPECT(frozen_flow.rows[0].valid == 1 && frozen_flow.rows[0].quality == 255);
    EXPECT(frozen_flow.rows[1].dx_px == 0 && frozen_flow.rows[1].dy_px == 0 && frozen_flow.rows[1].valid == 1);
    EXPECT(frozen_flow.rows[2].dx_px == 0 && frozen_flow.rows[2].dy_px == 0 && frozen_flow.rows[2].valid == 1);
    EXPECT(frozen_flow.rows[3].dx_px == 0 && frozen_flow.rows[3].dy_px == 2);
    const auto f1 = quadsim::read_pgm((frozen_dir.path / "frame_001.pgm").string());
    const auto f2 = quadsim::read_pgm((frozen_dir.path / "frame_002.pgm").string());
    const auto f3 = quadsim::read_pgm((frozen_dir.path / "frame_003.pgm").string());
    const auto f4 = quadsim::read_pgm((frozen_dir.path / "frame_004.pgm").string());
    EXPECT(f1.pixels == f2.pixels);
    EXPECT(f1.pixels == f3.pixels);
    EXPECT(f1.pixels != f4.pixels);
    expect_translation(f3, f4, 0, 2);
}

void test_stale_gap() {
    TempDir dir("stale");
    const auto result = generate_scenario(dir.path, "command_stale_gap", 1);
    EXPECT(result.classification == "valid_operationally_stale");
    const auto commands = quadsim::parse_commands(read_text(dir.path / "commands.csv"));
    EXPECT(commands.ok());
    EXPECT(commands.rows.size() == 4);
    const auto gaps = quadsim::command_timeout_gaps(commands.rows, quadsim::kInputTimeoutUs);
    EXPECT(gaps.size() == 1);
    EXPECT(gaps[0].gap_us == 680000);
    EXPECT(gaps[0].t_us == 700000);
    EXPECT(report_value(result.report_text, "max_gap_us") == "680000");
    EXPECT(report_value(result.report_text, "stale_gap_count") == "1");
    EXPECT(!std::filesystem::exists(dir.path / "flow.csv"));
    EXPECT(!std::filesystem::exists(dir.path / "manifest.csv"));
}

void expect_invalid_message(const std::filesystem::path& path, const std::string& kind,
                            const std::string& needle) {
    std::string message;
    bool ok = true;
    if (kind == "commands") {
        const auto parsed = quadsim::parse_commands(read_text(path));
        ok = parsed.ok();
        if (!ok) {
            message = parsed.error->message;
        }
    } else if (kind == "flow") {
        const auto parsed = quadsim::parse_flow(read_text(path));
        ok = parsed.ok();
        if (!ok) {
            message = parsed.error->message;
        }
    } else if (kind == "manifest") {
        const auto parsed = quadsim::parse_manifest(read_text(path));
        ok = parsed.ok();
        if (!ok) {
            message = parsed.error->message;
        }
    } else if (kind == "pgm") {
        try {
            quadsim::read_pgm(path.string());
            ok = true;
        } catch (const std::exception& error) {
            ok = false;
            message = error.what();
        }
    } else if (kind == "empty") {
        const auto parsed = quadsim::parse_commands(read_text(path));
        ok = parsed.ok();
        if (!ok) {
            message = parsed.error->message;
        }
    }
    EXPECT(!ok);
    EXPECT(message.find(needle) != std::string::npos);
}

void test_corrupt_suite() {
    TempDir dir("corrupt");
    const auto result = generate_scenario(dir.path, "corrupt_suite", 15);
    EXPECT(result.classification == "invalid_data");
    EXPECT(report_value(result.report_text, "seed") == "15");
    EXPECT(result.report_text.find("defect=invalid commands_nonfinite.csv") != std::string::npos);
    EXPECT(result.report_text.find("defect=valid_empty flow_header_only.csv") != std::string::npos);
    expect_invalid_message(dir.path / "commands_out_of_range.csv", "commands", "outside");
    expect_invalid_message(dir.path / "commands_duplicate_timestamp.csv", "commands", "strictly increasing");
    expect_invalid_message(dir.path / "commands_nonmonotonic.csv", "commands", "strictly increasing");
    expect_invalid_message(dir.path / "commands_bad_enabled.csv", "commands", "enabled");
    expect_invalid_message(dir.path / "commands_nonfinite.csv", "commands", "nonfinite");
    expect_invalid_message(dir.path / "commands_infinity.csv", "commands", "nonfinite");
    expect_invalid_message(dir.path / "commands_negative_time.csv", "commands", "nonfinite");
    expect_invalid_message(dir.path / "commands_quoted.csv", "commands", "quoted");
    expect_invalid_message(dir.path / "commands_crlf.csv", "commands", "CR");
    expect_invalid_message(dir.path / "commands_bad_header.csv", "commands", "header");
    expect_invalid_message(dir.path / "flow_duplicate_timestamp.csv", "flow", "strictly increasing");
    expect_invalid_message(dir.path / "flow_invalid_nonzero.csv", "flow", "zero displacement");
    expect_invalid_message(dir.path / "flow_invalid_quality.csv", "flow", "zero displacement");
    expect_invalid_message(dir.path / "flow_bad_quality.csv", "flow", "quality");
    expect_invalid_message(dir.path / "flow_unordered.csv", "flow", "strictly increasing");
    expect_invalid_message(dir.path / "flow_zero_dt.csv", "flow", "positive");
    expect_invalid_message(dir.path / "flow_dt_mismatch.csv", "flow", "gap");
    expect_invalid_message(dir.path / "manifest_parent_traversal.csv", "manifest", "unsafe");
    expect_invalid_message(dir.path / "manifest_absolute.csv", "manifest", "unsafe");
    expect_invalid_message(dir.path / "manifest_duplicate_time.csv", "manifest", "strictly increasing");
    expect_invalid_message(dir.path / "empty.csv", "empty", "empty");
    expect_invalid_message(dir.path / "frame_truncated.pgm", "pgm", "truncated");
    const auto empty_flow = quadsim::parse_flow(read_text(dir.path / "flow_header_only.csv"));
    EXPECT(empty_flow.ok());
    EXPECT(empty_flow.rows.empty());
    EXPECT(quadsim::manifest_filename_unsafe("foo/../../secret.pgm"));
    EXPECT(quadsim::manifest_filename_unsafe("/tmp/frame_000.pgm"));
    EXPECT(!quadsim::manifest_filename_unsafe("frame_000.pgm"));

    TempDir other("corrupt-b");
    generate_scenario(other.path, "corrupt_suite", 16);
    EXPECT(read_text(dir.path / "commands_out_of_range.csv") ==
           read_text(other.path / "commands_out_of_range.csv"));
    EXPECT(read_text(dir.path / "frame_truncated.pgm") != read_text(other.path / "frame_truncated.pgm"));
}

void test_evaluator_accounting() {
    const std::vector<quadsim::FlowRow> truth = {
        flow_row(20000, 20000, 0, 0, 255, 1),
        flow_row(40000, 20000, 1, -2, 255, 1),
        flow_row(60000, 20000, 0, 0, 0, 0),
    };
    auto perfect = quadsim::evaluate_flow(truth, truth, 0);
    EXPECT(perfect.overall_success);
    EXPECT(perfect.comparable_samples == 2);
    EXPECT(perfect.mean_displacement_error_px == 0);
    EXPECT(perfect.max_displacement_error_px == 0);
    EXPECT(perfect.valid_coverage == 1);
    EXPECT(perfect.false_valid_count == 0);

    const std::vector<quadsim::FlowRow> partial = {
        flow_row(20000, 20000, 3, 0, 255, 1),
        flow_row(40000, 20000, 1, 2, 255, 1),
        flow_row(60000, 20000, 1, 0, 255, 1),
        flow_row(80000, 20000, 0, 0, 255, 1),
    };
    auto partial_report = quadsim::evaluate_flow(truth, partial, 0);
    EXPECT(partial_report.inputs_well_formed);
    EXPECT(partial_report.comparable_samples == 2);
    EXPECT(std::fabs(partial_report.mean_displacement_error_px - 3.5) < 1e-9);
    EXPECT(std::fabs(partial_report.max_displacement_error_px - 4.0) < 1e-9);
    EXPECT(partial_report.missing_truth_records == 0);
    EXPECT(partial_report.valid_truth_missing == 0);
    EXPECT(partial_report.unmatched_estimate_records == 1);
    EXPECT(partial_report.false_valid_count == 1);
    EXPECT(partial_report.displacement_available);
    EXPECT(!partial_report.overall_success);

    const std::vector<quadsim::FlowRow> other_times = {
        flow_row(100000, 20000, 0, 0, 255, 1),
        flow_row(120000, 20000, 0, 0, 255, 1),
    };
    const std::vector<quadsim::FlowRow> truth_valid = {
        flow_row(20000, 20000, 4, 0, 255, 1),
        flow_row(40000, 20000, 0, 1, 255, 1),
    };
    auto disjoint = quadsim::evaluate_flow(truth_valid, other_times, 0);
    EXPECT(disjoint.comparable_samples == 0);
    EXPECT(!disjoint.displacement_available);
    EXPECT(disjoint.missing_truth_records == 2);
    EXPECT(disjoint.unmatched_estimate_records == 2);
    EXPECT(disjoint.valid_coverage == 0);
    EXPECT(!disjoint.overall_success);
    const std::string disjoint_text = quadsim::format_evaluation(disjoint);
    EXPECT(disjoint_text.find("mean_displacement_error_px=unavailable") != std::string::npos);
    EXPECT(disjoint_text.find("Displacement error is not reported as zero.") != std::string::npos);
    EXPECT(disjoint_text.find("mean_displacement_error_px=0") == std::string::npos);

    auto duplicates = truth;
    duplicates.push_back(flow_row(60000, 20000, 0, 0, 0, 0));
    auto duplicate_report = quadsim::evaluate_flow(duplicates, truth, 0);
    EXPECT(!duplicate_report.inputs_well_formed);
    EXPECT(!duplicate_report.displacement_available);
    EXPECT(!duplicate_report.overall_success);
    EXPECT(duplicate_report.duplicate_timestamp_values >= 1);

    const std::string bad_truth =
        "t_us,dt_us,dx_px,dy_px,quality,valid\n20000,20000,2,0,0,0\n";
    const std::string good_estimate =
        "t_us,dt_us,dx_px,dy_px,quality,valid\n20000,20000,0,0,0,0\n";
    auto malformed = quadsim::evaluate_flow_text(bad_truth, good_estimate, 0);
    EXPECT(!malformed.inputs_well_formed);
    EXPECT(!malformed.displacement_available);
    EXPECT(malformed.schema_error.find("zero displacement") != std::string::npos);
    EXPECT(quadsim::format_evaluation(malformed).find("mean_displacement_error_px=unavailable") !=
           std::string::npos);

    const std::vector<quadsim::FlowRow> within = {
        flow_row(20000, 20000, 0.4, 0, 200, 1),
        flow_row(40000, 20000, 1, -2.2, 200, 1),
        flow_row(60000, 20000, 0, 0, 0, 0),
    };
    auto tolerant = quadsim::evaluate_flow(truth, within, 0.5);
    EXPECT(tolerant.overall_success);
    EXPECT(tolerant.max_displacement_error_px <= 0.5);
    auto strict = quadsim::evaluate_flow(truth, within, 0);
    EXPECT(!strict.overall_success);
    EXPECT(strict.displacement_success == 0);
}

void test_overwrite_frames_and_cli() {
    TempDir dir("overwrite");
    generate_scenario(dir.path, "nominal", 1);
    bool refused = false;
    try {
        generate_scenario(dir.path, "nominal", 1);
    } catch (const quadsim::OverwriteRefused&) {
        refused = true;
    }
    EXPECT(refused);
    std::ofstream keep(dir.path / "keep.txt");
    keep << "retain\n";
    keep.close();
    generate_scenario(dir.path, "nominal", 3, true);
    EXPECT(read_text(dir.path / "keep.txt") == "retain\n");
    EXPECT(report_value(read_text(dir.path / "scenario_report.txt"), "seed") == "3");

    TempDir file_dir("file-out");
    const auto file_path = file_dir.path / "not-a-directory";
    std::ofstream file_out(file_path);
    file_out << "x\n";
    file_out.close();
    bool file_refused = false;
    try {
        generate_scenario(file_path, "nominal", 1);
    } catch (const std::runtime_error& error) {
        file_refused = std::string(error.what()).find("not a directory") != std::string::npos;
    }
    EXPECT(file_refused);

    TempDir link_dir("link");
    const auto real = link_dir.path / "real";
    const auto link = link_dir.path / "link";
    std::filesystem::create_directory(real);
    std::filesystem::create_directory_symlink(real, link);
    bool link_refused = false;
    try {
        generate_scenario(link, "nominal", 1);
    } catch (const std::runtime_error& error) {
        link_refused = std::string(error.what()).find("symlink") != std::string::npos;
    }
    EXPECT(link_refused);

    bool bad_frames = false;
    try {
        generate_scenario(dir.path / "too-small", "nominal", 1, false, 1);
    } catch (const std::runtime_error& error) {
        bad_frames = std::string(error.what()).find("frames") != std::string::npos;
    }
    EXPECT(bad_frames);
    bool cardinal_frames = false;
    try {
        generate_scenario(dir.path / "card-frames", "translation_cardinal", 1, false, 4);
    } catch (const std::runtime_error& error) {
        cardinal_frames = std::string(error.what()).find("nominal") != std::string::npos;
    }
    EXPECT(cardinal_frames);
    TempDir short_dir("frames4");
    const auto short_run = generate_scenario(short_dir.path, "nominal", 1, false, 4);
    EXPECT(report_value(short_run.report_text, "frame_count") == "4");
    EXPECT(report_value(short_run.report_text, "shifts") == "1:0,0:-1,2:1");
    EXPECT(quadsim::parse_flow(read_text(short_dir.path / "flow.csv")).rows.size() == 3);

    int code = -1;
    const std::string listed = capture(quote(g_cli) + " list-scenarios", code);
    EXPECT(code == 0);
    EXPECT(listed.find("frozen_frames") != std::string::npos);
    EXPECT(listed.find("valid_physically_ambiguous") != std::string::npos);

    TempDir cli_dir("cli");
    const std::string once = quote(g_cli) + " generate --scenario nominal --seed 4 --output " +
                             quote(cli_dir.path.string());
    capture(once, code);
    EXPECT(code == 0);
    capture(once, code);
    EXPECT(code == 4);
    capture(once + " --overwrite", code);
    EXPECT(code == 0);

    const auto flow = cli_dir.path / "flow.csv";
    const auto report = cli_dir.path / "eval.txt";
    capture(quote(g_cli) + " evaluate --truth " + quote(flow.string()) + " --estimate " +
                quote(flow.string()) + " --output " + quote(report.string()),
            code);
    EXPECT(code == 0);
    const std::string perfect = read_text(report);
    EXPECT(perfect.find("overall_success=1") != std::string::npos);
    EXPECT(perfect.find("mean_displacement_error_px=0.000000") != std::string::npos);
    EXPECT(perfect.find("comparable_samples=5") != std::string::npos);

    TempDir disjoint_dir("cli-disjoint");
    const std::string other_flow =
        "t_us,dt_us,dx_px,dy_px,quality,valid\n200000,20000,0,0,255,1\n220000,20000,0,0,255,1\n";
    std::ofstream other(disjoint_dir.path / "other.csv");
    other << other_flow;
    other.close();
    const auto disjoint_report = disjoint_dir.path / "eval.txt";
    const std::string disjoint_stdout =
        capture(quote(g_cli) + " evaluate --truth " + quote(flow.string()) + " --estimate " +
                    quote((disjoint_dir.path / "other.csv").string()) + " --output " +
                    quote(disjoint_report.string()),
                code);
    EXPECT(code == 3);
    EXPECT(disjoint_stdout.find("mean_displacement_error_px=unavailable") != std::string::npos);
    EXPECT(read_text(disjoint_report).find("overall_success=0") != std::string::npos);

    capture(quote(g_cli) + " check-schema --commands " + quote((cli_dir.path / "commands.csv").string()),
            code);
    EXPECT(code == 0);
    TempDir stale_dir("cli-stale");
    generate_scenario(stale_dir.path, "command_stale_gap", 1);
    const std::string stale_out = capture(
        quote(g_cli) + " check-schema --commands " + quote((stale_dir.path / "commands.csv").string()),
        code);
    EXPECT(code == 0);
    EXPECT(stale_out.find("ok") != std::string::npos);
    EXPECT(stale_out.find("680000") != std::string::npos);

    TempDir bad_dir("cli-bad");
    generate_scenario(bad_dir.path, "corrupt_suite", 1);
    const std::string bad_out = capture(quote(g_cli) + " check-schema --flow " +
                                            quote((bad_dir.path / "flow_invalid_nonzero.csv").string()),
                                        code);
    EXPECT(code == 2);
    EXPECT(bad_out.find("invalid:") != std::string::npos);
    capture(quote(g_cli) + " check-schema --manifest " +
                quote((bad_dir.path / "manifest_parent_traversal.csv").string()),
            code);
    EXPECT(code == 2);
    capture(quote(g_cli) + " generate --scenario missing --output " + quote(bad_dir.path.string()) +
                " --overwrite",
            code);
    EXPECT(code == 1);
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 1) {
        return 2;
    }
    g_cli = (std::filesystem::absolute(argv[0]).parent_path() / "quad-sim").string();
    try {
        test_catalog();
        test_determinism();
        test_cardinal_translation();
        test_nominal_schema();
        test_low_texture();
        test_noise_and_dropped_and_frozen();
        test_stale_gap();
        test_corrupt_suite();
        test_evaluator_accounting();
        test_overwrite_frames_and_cli();
    } catch (const std::exception& error) {
        std::cerr << "uncaught: " << error.what() << '\n';
        return 1;
    }
    std::cout << "passed=" << g_passed << " failed=" << g_failed << '\n';
    return g_failed == 0 ? 0 : 1;
}
