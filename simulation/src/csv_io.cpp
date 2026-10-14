#include "quadsim/csv_io.hpp"

#include <cmath>
#include <filesystem>
#include <sstream>
#include <utility>

namespace quadsim {
namespace {

constexpr const char* kCommandHeader = "t_us,forward,right,up,yaw_cw,enabled";
constexpr const char* kFlowHeader = "t_us,dt_us,dx_px,dy_px,quality,valid";
constexpr const char* kManifestHeader = "t_us,filename";

struct Line {
    int number = 0;
    std::string text;
};

ParseError err_at(int line, std::string message) {
    return ParseError{line, std::move(message)};
}

bool split_lines(const std::string& text, std::vector<Line>& lines, ParseError& error) {
    if (text.empty()) {
        error = err_at(0, "file is empty");
        return false;
    }
    if (text.find('\r') != std::string::npos) {
        error = err_at(0, "CR is not allowed; use LF newlines");
        return false;
    }
    if (text.size() >= 3 && static_cast<unsigned char>(text[0]) == 0xEF) {
        error = err_at(1, "UTF-8 BOM is not allowed");
        return false;
    }
    int number = 1;
    std::string current;
    for (char c : text) {
        if (c == '\n') {
            lines.push_back(Line{number, current});
            current.clear();
            ++number;
        } else {
            current.push_back(c);
        }
    }
    if (!current.empty()) {
        lines.push_back(Line{number, current});
    }
    if (!lines.empty() && lines.back().text.empty()) {
        lines.pop_back();
    }
    if (lines.empty()) {
        error = err_at(0, "file is empty");
        return false;
    }
    return true;
}

bool split_fields(const Line& line, std::size_t expected, std::vector<std::string>& fields,
                  ParseError& error) {
    if (line.text.find('"') != std::string::npos || line.text.find('\'') != std::string::npos) {
        error = err_at(line.number, "quoted fields are not allowed");
        return false;
    }
    fields.clear();
    std::string current;
    for (char c : line.text) {
        if (c == ',') {
            fields.push_back(current);
            current.clear();
        } else {
            current.push_back(c);
        }
    }
    fields.push_back(current);
    if (fields.size() != expected) {
        error = err_at(line.number, "unexpected column count");
        return false;
    }
    for (const auto& field : fields) {
        if (field.empty()) {
            error = err_at(line.number, "empty field");
            return false;
        }
        if (field.find_first_of(" \t") != std::string::npos) {
            error = err_at(line.number, "whitespace in field");
            return false;
        }
    }
    return true;
}

bool parse_u64(const std::string& token, std::uint64_t& out) {
    if (token.empty() || token[0] == '+' || token[0] == '-') {
        return false;
    }
    if (token[0] == '0' && token.size() != 1) {
        return false;
    }
    std::uint64_t value = 0;
    for (char c : token) {
        if (c < '0' || c > '9') {
            return false;
        }
        const std::uint64_t digit = static_cast<std::uint64_t>(c - '0');
        if (value > (UINT64_MAX - digit) / 10) {
            return false;
        }
        value = value * 10 + digit;
    }
    out = value;
    return true;
}

bool decimal_token(const std::string& token) {
    if (token.empty()) {
        return false;
    }
    std::size_t i = 0;
    if (token[0] == '+' || token[0] == '-') {
        i = 1;
    }
    if (i >= token.size()) {
        return false;
    }
    bool digit = false;
    bool dot = false;
    for (; i < token.size(); ++i) {
        const char c = token[i];
        if (c == '.') {
            if (dot) {
                return false;
            }
            dot = true;
        } else if (c >= '0' && c <= '9') {
            digit = true;
        } else {
            return false;
        }
    }
    return digit;
}

bool parse_finite(const std::string& token, double& out) {
    if (!decimal_token(token)) {
        return false;
    }
    std::istringstream input(token);
    input.imbue(std::locale::classic());
    double value = 0;
    input >> value;
    if (!input || !input.eof() || !std::isfinite(value)) {
        return false;
    }
    out = value;
    return true;
}

bool axis_in_range(double value) {
    return value >= -1.0 && value <= 1.0;
}

std::string trim_decimal(std::string text) {
    const auto dot = text.find('.');
    if (dot == std::string::npos) {
        return text;
    }
    while (!text.empty() && text.back() == '0') {
        text.pop_back();
    }
    if (!text.empty() && text.back() == '.') {
        text.pop_back();
    }
    if (text == "-0" || text.empty()) {
        return "0";
    }
    return text;
}

}  // namespace

std::string format_decimal(double value) {
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out.setf(std::ios::fixed);
    out.precision(6);
    out << value;
    return trim_decimal(out.str());
}

std::string format_commands(const std::vector<CommandRow>& rows) {
    std::ostringstream out;
    out << kCommandHeader << '\n';
    for (const auto& row : rows) {
        out << row.t_us << ',' << format_decimal(row.forward) << ',' << format_decimal(row.right)
            << ',' << format_decimal(row.up) << ',' << format_decimal(row.yaw_cw) << ','
            << row.enabled << '\n';
    }
    return out.str();
}

std::string format_flow(const std::vector<FlowRow>& rows) {
    std::ostringstream out;
    out << kFlowHeader << '\n';
    for (const auto& row : rows) {
        out << row.t_us << ',' << row.dt_us << ',' << format_decimal(row.dx_px) << ','
            << format_decimal(row.dy_px) << ',' << row.quality << ',' << row.valid << '\n';
    }
    return out.str();
}

std::string format_manifest(const std::vector<ManifestRow>& rows) {
    std::ostringstream out;
    out << kManifestHeader << '\n';
    for (const auto& row : rows) {
        out << row.t_us << ',' << row.filename << '\n';
    }
    return out.str();
}

CommandParse parse_commands(const std::string& text) {
    CommandParse result;
    ParseError error;
    std::vector<Line> lines;
    if (!split_lines(text, lines, error)) {
        result.error = error;
        return result;
    }
    if (lines.front().text != kCommandHeader) {
        result.error = err_at(lines.front().number, "commands header mismatch");
        return result;
    }
    for (std::size_t i = 1; i < lines.size(); ++i) {
        std::vector<std::string> fields;
        if (!split_fields(lines[i], 6, fields, error)) {
            result.error = error;
            return result;
        }
        CommandRow row;
        double enabled = 0;
        if (!parse_u64(fields[0], row.t_us) || !parse_finite(fields[1], row.forward) ||
            !parse_finite(fields[2], row.right) || !parse_finite(fields[3], row.up) ||
            !parse_finite(fields[4], row.yaw_cw) || !parse_finite(fields[5], enabled)) {
            result.error = err_at(lines[i].number, "nonfinite or non-numeric command field");
            return result;
        }
        if (!axis_in_range(row.forward) || !axis_in_range(row.right) || !axis_in_range(row.up) ||
            !axis_in_range(row.yaw_cw)) {
            result.error = err_at(lines[i].number, "axis outside [-1,1]");
            return result;
        }
        if (enabled != 0.0 && enabled != 1.0) {
            result.error = err_at(lines[i].number, "enabled must be 0 or 1");
            return result;
        }
        row.enabled = enabled == 1.0 ? 1 : 0;
        if (!result.rows.empty() && row.t_us <= result.rows.back().t_us) {
            result.error = err_at(lines[i].number, "timestamps must be strictly increasing");
            return result;
        }
        result.rows.push_back(row);
    }
    return result;
}

FlowParse parse_flow(const std::string& text) {
    FlowParse result;
    ParseError error;
    std::vector<Line> lines;
    if (!split_lines(text, lines, error)) {
        result.error = error;
        return result;
    }
    if (lines.front().text != kFlowHeader) {
        result.error = err_at(lines.front().number, "flow header mismatch");
        return result;
    }
    for (std::size_t i = 1; i < lines.size(); ++i) {
        std::vector<std::string> fields;
        if (!split_fields(lines[i], 6, fields, error)) {
            result.error = error;
            return result;
        }
        FlowRow row;
        double quality = 0;
        double valid = 0;
        if (!parse_u64(fields[0], row.t_us) || !parse_u64(fields[1], row.dt_us) ||
            !parse_finite(fields[2], row.dx_px) || !parse_finite(fields[3], row.dy_px) ||
            !parse_finite(fields[4], quality) || !parse_finite(fields[5], valid)) {
            result.error = err_at(lines[i].number, "nonfinite or non-numeric flow field");
            return result;
        }
        if (quality < 0.0 || quality > 255.0 || quality != std::floor(quality)) {
            result.error = err_at(lines[i].number, "quality must be an integer in [0,255]");
            return result;
        }
        if (valid != 0.0 && valid != 1.0) {
            result.error = err_at(lines[i].number, "valid must be 0 or 1");
            return result;
        }
        row.quality = static_cast<int>(quality);
        row.valid = valid == 1.0 ? 1 : 0;
        if (row.dt_us == 0) {
            result.error = err_at(lines[i].number, "dt_us must be positive");
            return result;
        }
        if (row.t_us < row.dt_us) {
            result.error = err_at(lines[i].number, "pair would start before t_us 0");
            return result;
        }
        if (row.valid == 0 && (row.dx_px != 0.0 || row.dy_px != 0.0 || row.quality != 0)) {
            result.error = err_at(lines[i].number,
                                  "invalid flow must have zero displacement and quality 0");
            return result;
        }
        if (!result.rows.empty()) {
            if (row.t_us <= result.rows.back().t_us) {
                result.error = err_at(lines[i].number, "timestamps must be strictly increasing");
                return result;
            }
            if (row.t_us - result.rows.back().t_us != row.dt_us) {
                result.error = err_at(lines[i].number,
                                      "dt_us must equal the gap from the previous flow timestamp");
                return result;
            }
        }
        result.rows.push_back(row);
    }
    return result;
}

ManifestParse parse_manifest(const std::string& text) {
    ManifestParse result;
    ParseError error;
    std::vector<Line> lines;
    if (!split_lines(text, lines, error)) {
        result.error = error;
        return result;
    }
    if (lines.front().text != kManifestHeader) {
        result.error = err_at(lines.front().number, "manifest header mismatch");
        return result;
    }
    for (std::size_t i = 1; i < lines.size(); ++i) {
        std::vector<std::string> fields;
        if (!split_fields(lines[i], 2, fields, error)) {
            result.error = error;
            return result;
        }
        ManifestRow row;
        if (!parse_u64(fields[0], row.t_us)) {
            result.error = err_at(lines[i].number, "manifest timestamp must be a nonnegative integer");
            return result;
        }
        row.filename = fields[1];
        if (manifest_filename_unsafe(row.filename)) {
            result.error = err_at(lines[i].number, "unsafe manifest path");
            return result;
        }
        if (!result.rows.empty() && row.t_us <= result.rows.back().t_us) {
            result.error = err_at(lines[i].number, "timestamps must be strictly increasing");
            return result;
        }
        result.rows.push_back(row);
    }
    return result;
}

std::vector<TimingGap> command_timeout_gaps(const std::vector<CommandRow>& rows,
                                            std::uint64_t timeout_us) {
    std::vector<TimingGap> gaps;
    for (std::size_t i = 1; i < rows.size(); ++i) {
        const std::uint64_t gap = rows[i].t_us - rows[i - 1].t_us;
        if (gap > timeout_us) {
            gaps.push_back(TimingGap{rows[i].t_us, gap});
        }
    }
    return gaps;
}

bool manifest_filename_unsafe(const std::string& filename) {
    if (filename.empty() || filename == "." || filename == "..") {
        return true;
    }
    if (filename.find('\\') != std::string::npos || filename.find(':') != std::string::npos) {
        return true;
    }
    const std::filesystem::path path(filename);
    if (path.is_absolute()) {
        return true;
    }
    for (const auto& part : path) {
        if (part == "..") {
            return true;
        }
    }
    const auto normal = path.lexically_normal();
    if (!normal.empty() && *normal.begin() == "..") {
        return true;
    }
    return false;
}

}  // namespace quadsim
