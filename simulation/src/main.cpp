#include "quadsim/csv_io.hpp"
#include "quadsim/evaluate.hpp"
#include "quadsim/generate.hpp"
#include "quadsim/types.hpp"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <string>

namespace {

void usage() {
    std::cerr
        << "quad-sim generate --scenario NAME --output DIR [--seed N] [--frames N] [--overwrite]\n"
        << "quad-sim evaluate --truth FLOW.csv --estimate FLOW.csv --output REPORT [--tolerance-px N]\n"
        << "quad-sim check-schema (--commands PATH | --flow PATH | --manifest PATH)\n"
        << "quad-sim list-scenarios\n"
        << "\n"
        << "Fixture generation refuses a non-empty output directory unless --overwrite is set.\n"
        << "--overwrite replaces files this scenario writes and leaves unrelated names in place.\n"
        << "evaluate writes its report even when the result is a failure.\n"
        << "Exit codes: 0 success, 1 usage or runtime error, 2 malformed input, "
        << "3 evaluation failed, 4 overwrite refused.\n";
}

struct Flags {
    std::map<std::string, std::string> values;
    bool overwrite = false;
};

Flags parse_flags(int argc, char** argv, int begin) {
    Flags flags;
    for (int i = begin; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg.rfind("--", 0) != 0 || arg.size() < 3) {
            throw std::runtime_error("unexpected argument: " + arg);
        }
        const std::string key = arg.substr(2);
        if (key == "overwrite") {
            if (flags.overwrite) {
                throw std::runtime_error("duplicate flag --overwrite");
            }
            flags.overwrite = true;
            continue;
        }
        if (i + 1 >= argc) {
            throw std::runtime_error("missing value for --" + key);
        }
        if (flags.values.count(key) != 0) {
            throw std::runtime_error("duplicate flag --" + key);
        }
        flags.values.emplace(key, argv[++i]);
    }
    return flags;
}

std::string require_flag(const Flags& flags, const std::string& key) {
    const auto found = flags.values.find(key);
    if (found == flags.values.end() || found->second.empty()) {
        throw std::runtime_error("missing --" + key);
    }
    return found->second;
}

std::string read_text(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw std::runtime_error("failed to read " + path.string());
    }
    std::ostringstream buffer;
    buffer << input.rdbuf();
    return buffer.str();
}

int write_text(const std::filesystem::path& path, const std::string& text) {
    if (path.empty()) {
        throw std::runtime_error("output path is empty");
    }
    if (std::filesystem::exists(path) && std::filesystem::is_directory(path)) {
        throw std::runtime_error("output path is a directory");
    }
    if (path.has_parent_path() && !path.parent_path().empty()) {
        std::filesystem::create_directories(path.parent_path());
    }
    std::ofstream output(path, std::ios::binary);
    if (!output) {
        throw std::runtime_error("failed to write " + path.string());
    }
    output << text;
    if (!output) {
        throw std::runtime_error("failed to write " + path.string());
    }
    return 0;
}

int command_list() {
    std::size_t count = 0;
    const quadsim::ScenarioInfo* catalog = quadsim::scenario_catalog(count);
    for (std::size_t i = 0; i < count; ++i) {
        std::cout << catalog[i].name << '\t' << catalog[i].classification << '\t'
                  << catalog[i].summary << '\n';
    }
    return 0;
}

int command_generate(const Flags& flags) {
    quadsim::GenerateRequest request;
    request.scenario = require_flag(flags, "scenario");
    request.output_dir = require_flag(flags, "output");
    request.overwrite = flags.overwrite;
    const auto seed = flags.values.find("seed");
    if (seed != flags.values.end()) {
        if (seed->second.empty() || seed->second[0] == '-' || seed->second[0] == '+') {
            throw std::runtime_error("--seed must be an unsigned integer");
        }
        std::uint64_t value = 0;
        for (char c : seed->second) {
            if (c < '0' || c > '9') {
                throw std::runtime_error("--seed must be an unsigned integer");
            }
            value = value * 10 + static_cast<std::uint64_t>(c - '0');
            if (value > 0xFFFFFFFFull) {
                throw std::runtime_error("--seed is out of range");
            }
        }
        request.seed = static_cast<std::uint32_t>(value);
    }
    const auto frames = flags.values.find("frames");
    if (frames != flags.values.end()) {
        int value = 0;
        for (char c : frames->second) {
            if (c < '0' || c > '9') {
                throw std::runtime_error("--frames must be an integer");
            }
            value = value * 10 + (c - '0');
        }
        request.frames = value;
    }
    for (const auto& entry : flags.values) {
        if (entry.first != "scenario" && entry.first != "output" && entry.first != "seed" &&
            entry.first != "frames") {
            throw std::runtime_error("unknown flag --" + entry.first);
        }
    }
    const quadsim::GenerateResult result = quadsim::generate(request);
    std::cout << "wrote " << result.output_dir.string() << '\n';
    std::cout << "classification=" << result.classification << '\n';
    std::cout << "report=" << (result.output_dir / "scenario_report.txt").string() << '\n';
    return 0;
}

int command_evaluate(const Flags& flags) {
    const auto truth_path = require_flag(flags, "truth");
    const auto estimate_path = require_flag(flags, "estimate");
    const auto output_path = require_flag(flags, "output");
    double tolerance = 0;
    const auto tolerance_flag = flags.values.find("tolerance-px");
    if (tolerance_flag != flags.values.end()) {
        std::istringstream input(tolerance_flag->second);
        input.imbue(std::locale::classic());
        input >> tolerance;
        if (!input || !input.eof()) {
            throw std::runtime_error("--tolerance-px must be a number");
        }
    }
    for (const auto& entry : flags.values) {
        if (entry.first != "truth" && entry.first != "estimate" && entry.first != "output" &&
            entry.first != "tolerance-px") {
            throw std::runtime_error("unknown flag --" + entry.first);
        }
    }
    if (flags.overwrite) {
        throw std::runtime_error("evaluate does not take --overwrite");
    }
    const quadsim::EvalReport report = quadsim::evaluate_flow_text(
        read_text(truth_path), read_text(estimate_path), tolerance);
    const std::string text = quadsim::format_evaluation(report);
    write_text(output_path, text);
    std::cout << text;
    if (!report.inputs_well_formed) {
        return 2;
    }
    if (!report.overall_success) {
        return 3;
    }
    return 0;
}

int command_check(const Flags& flags) {
    const bool has_commands = flags.values.count("commands") != 0;
    const bool has_flow = flags.values.count("flow") != 0;
    const bool has_manifest = flags.values.count("manifest") != 0;
    if (static_cast<int>(has_commands) + static_cast<int>(has_flow) + static_cast<int>(has_manifest) !=
        1) {
        throw std::runtime_error("check-schema requires exactly one of --commands, --flow, --manifest");
    }
    if (flags.overwrite) {
        throw std::runtime_error("check-schema does not take --overwrite");
    }
    if (has_commands) {
        const auto parsed = quadsim::parse_commands(read_text(flags.values.at("commands")));
        if (!parsed.ok()) {
            std::cout << "invalid: line " << parsed.error->line << ": " << parsed.error->message
                      << '\n';
            return 2;
        }
        std::cout << "ok\n";
        for (const auto& gap : quadsim::command_timeout_gaps(parsed.rows, quadsim::kInputTimeoutUs)) {
            std::cout << "warning: gap " << gap.gap_us << " us exceeds " << quadsim::kInputTimeoutUs
                      << " us input timeout at t_us=" << gap.t_us << '\n';
        }
        return 0;
    }
    if (has_flow) {
        const auto parsed = quadsim::parse_flow(read_text(flags.values.at("flow")));
        if (!parsed.ok()) {
            std::cout << "invalid: line " << parsed.error->line << ": " << parsed.error->message
                      << '\n';
            return 2;
        }
        std::cout << "ok\n";
        return 0;
    }
    const auto parsed = quadsim::parse_manifest(read_text(flags.values.at("manifest")));
    if (!parsed.ok()) {
        std::cout << "invalid: line " << parsed.error->line << ": " << parsed.error->message << '\n';
        return 2;
    }
    std::cout << "ok\n";
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc < 2) {
            usage();
            return 1;
        }
        const std::string command = argv[1];
        if (command == "help" || command == "--help" || command == "-h") {
            usage();
            return 0;
        }
        if (command == "list-scenarios") {
            if (argc != 2) {
                usage();
                return 1;
            }
            return command_list();
        }
        const Flags flags = parse_flags(argc, argv, 2);
        if (command == "generate") {
            return command_generate(flags);
        }
        if (command == "evaluate") {
            return command_evaluate(flags);
        }
        if (command == "check-schema") {
            return command_check(flags);
        }
        usage();
        return 1;
    } catch (const quadsim::OverwriteRefused& error) {
        std::cerr << error.what() << '\n';
        return 4;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
