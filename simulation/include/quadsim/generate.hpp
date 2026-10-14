#pragma once

#include <cstdint>
#include <filesystem>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace quadsim {

struct OverwriteRefused : std::runtime_error {
    using std::runtime_error::runtime_error;
};

struct GenerateRequest {
    std::string scenario;
    std::filesystem::path output_dir;
    std::uint32_t seed = 1;
    bool overwrite = false;
    std::optional<int> frames;
};

struct GenerateResult {
    std::string scenario;
    std::string classification;
    std::filesystem::path output_dir;
    std::vector<std::filesystem::path> files;
    std::string report_text;
};

struct ScenarioInfo {
    const char* name;
    const char* classification;
    const char* summary;
};

const ScenarioInfo* scenario_catalog(std::size_t& count);

GenerateResult generate(const GenerateRequest& request);

}  // namespace quadsim
