#include "quadsim/evaluate.hpp"

#include "quadsim/csv_io.hpp"

#include <cmath>
#include <iomanip>
#include <map>
#include <sstream>
#include <utility>

namespace quadsim {
namespace {

const char* tri_state(int value) {
    if (value < 0) {
        return "not_applicable";
    }
    return value == 1 ? "1" : "0";
}

std::string fixed6(double value) {
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << std::fixed << std::setprecision(6) << value;
    return out.str();
}

EvalReport malformed(std::string message, int truth_rows, int estimate_rows, double tolerance) {
    EvalReport report;
    report.inputs_well_formed = false;
    report.schema_error = std::move(message);
    report.truth_rows = truth_rows;
    report.estimate_rows = estimate_rows;
    report.tolerance_px = tolerance;
    report.notes =
        "Inputs are malformed. Displacement error is not reported as zero.";
    return report;
}

}  // namespace

EvalReport evaluate_flow(const std::vector<FlowRow>& truth, const std::vector<FlowRow>& estimate,
                         double tolerance_px) {
    if (!std::isfinite(tolerance_px) || tolerance_px < 0.0) {
        return malformed("tolerance_px must be a nonnegative finite number",
                         static_cast<int>(truth.size()), static_cast<int>(estimate.size()),
                         tolerance_px);
    }

    auto check_unique = [](const std::vector<FlowRow>& rows, int& duplicate_values) {
        std::map<std::uint64_t, int> seen;
        for (const auto& row : rows) {
            ++seen[row.t_us];
        }
        for (const auto& entry : seen) {
            if (entry.second > 1) {
                ++duplicate_values;
            }
        }
        for (std::size_t i = 1; i < rows.size(); ++i) {
            if (rows[i].t_us <= rows[i - 1].t_us) {
                return false;
            }
            if (rows[i].t_us - rows[i - 1].t_us != rows[i].dt_us) {
                return false;
            }
        }
        for (const auto& row : rows) {
            if (row.dt_us == 0 || row.t_us < row.dt_us) {
                return false;
            }
            if (row.quality < 0 || row.quality > 255 || (row.valid != 0 && row.valid != 1)) {
                return false;
            }
            if (!std::isfinite(row.dx_px) || !std::isfinite(row.dy_px)) {
                return false;
            }
            if (row.valid == 0 && (row.dx_px != 0.0 || row.dy_px != 0.0 || row.quality != 0)) {
                return false;
            }
        }
        return duplicate_values == 0;
    };

    int duplicate_values = 0;
    const bool truth_ok = check_unique(truth, duplicate_values);
    const bool estimate_ok = check_unique(estimate, duplicate_values);
    if (!truth_ok || !estimate_ok) {
        EvalReport report = malformed(
            "flow rows are duplicate, unordered, or violate the valid-sample schema",
            static_cast<int>(truth.size()), static_cast<int>(estimate.size()), tolerance_px);
        report.duplicate_timestamp_values = duplicate_values;
        return report;
    }

    EvalReport report;
    report.inputs_well_formed = true;
    report.tolerance_px = tolerance_px;
    report.truth_rows = static_cast<int>(truth.size());
    report.estimate_rows = static_cast<int>(estimate.size());

    std::map<std::uint64_t, const FlowRow*> estimate_by_time;
    for (const auto& row : estimate) {
        estimate_by_time.emplace(row.t_us, &row);
    }
    std::map<std::uint64_t, const FlowRow*> truth_by_time;
    for (const auto& row : truth) {
        truth_by_time.emplace(row.t_us, &row);
    }

    double error_sum = 0;
    double error_max = 0;
    for (const auto& row : truth) {
        if (row.valid == 1) {
            ++report.truth_valid_rows;
        }
        const auto found = estimate_by_time.find(row.t_us);
        if (found == estimate_by_time.end()) {
            ++report.missing_truth_records;
            if (row.valid == 1) {
                ++report.valid_truth_missing;
            }
            continue;
        }
        ++report.matched_rows;
        const FlowRow& other = *found->second;
        if (row.valid == 0 && other.valid == 1) {
            ++report.false_valid_count;
        }
        if (row.valid == 1 && other.valid == 0) {
            ++report.missed_valid_count;
        }
        if (row.valid == 1 && other.valid == 1) {
            ++report.covered_valid_rows;
            ++report.comparable_samples;
            const double error = std::hypot(other.dx_px - row.dx_px, other.dy_px - row.dy_px);
            error_sum += error;
            if (error > error_max) {
                error_max = error;
            }
        }
    }
    for (const auto& row : estimate) {
        if (truth_by_time.find(row.t_us) == truth_by_time.end()) {
            ++report.unmatched_estimate_records;
        }
    }

    if (report.truth_valid_rows > 0) {
        report.valid_coverage_available = true;
        report.valid_coverage = static_cast<double>(report.covered_valid_rows) /
                                static_cast<double>(report.truth_valid_rows);
    }
    if (report.comparable_samples > 0) {
        report.displacement_available = true;
        report.mean_displacement_error_px = error_sum / static_cast<double>(report.comparable_samples);
        report.max_displacement_error_px = error_max;
        report.displacement_success =
            report.max_displacement_error_px <= tolerance_px + 1e-9 ? 1 : 0;
    }

    const bool invalid_agreement =
        report.truth_rows > 0 && report.truth_valid_rows == 0 && report.false_valid_count == 0 &&
        report.missing_truth_records == 0 && report.unmatched_estimate_records == 0 &&
        report.matched_rows == report.truth_rows;
    if (report.truth_valid_rows == 0) {
        report.rejection_success = invalid_agreement ? 1 : 0;
    }

    const bool aligned = report.missing_truth_records == 0 && report.unmatched_estimate_records == 0 &&
                         report.false_valid_count == 0 && report.missed_valid_count == 0;
    if (report.truth_valid_rows > 0) {
        report.overall_success = aligned && report.comparable_samples > 0 &&
                                 report.displacement_success == 1;
    } else {
        report.overall_success = invalid_agreement;
    }

    if (report.comparable_samples == 0 && report.truth_valid_rows == 0 && report.overall_success) {
        report.notes =
            "No comparable valid samples. Displacement error is not reported as zero. "
            "Overall success reflects agreement on invalid truth only.";
    } else if (report.comparable_samples == 0) {
        report.notes =
            "No comparable valid samples. Displacement error is not reported as zero.";
    } else {
        report.notes =
            "Comparable samples are timestamp-aligned rows where both truth and estimate set valid=1.";
    }
    return report;
}

EvalReport evaluate_flow_text(const std::string& truth_text, const std::string& estimate_text,
                              double tolerance_px) {
    const FlowParse truth = parse_flow(truth_text);
    const FlowParse estimate = parse_flow(estimate_text);
    if (!truth.ok() || !estimate.ok()) {
        const ParseError& failure = !truth.ok() ? *truth.error : *estimate.error;
        const std::string which = !truth.ok() ? "truth" : "estimate";
        EvalReport report = malformed(
            which + " line " + std::to_string(failure.line) + ": " + failure.message,
            static_cast<int>(truth.rows.size()), static_cast<int>(estimate.rows.size()),
            tolerance_px);
        if (failure.message.find("strictly increasing") != std::string::npos) {
            report.duplicate_timestamp_values = 1;
        }
        return report;
    }
    return evaluate_flow(truth.rows, estimate.rows, tolerance_px);
}

std::string format_evaluation(const EvalReport& report) {
    std::ostringstream out;
    out << "quad-sim evaluation report\n";
    out << "schema_version=1\n";
    out << "inputs_well_formed=" << (report.inputs_well_formed ? 1 : 0) << '\n';
    out << "schema_error=" << (report.schema_error.empty() ? "none" : report.schema_error) << '\n';
    out << "truth_rows=" << report.truth_rows << '\n';
    out << "estimate_rows=" << report.estimate_rows << '\n';
    out << "matched_rows=" << report.matched_rows << '\n';
    out << "truth_valid_rows=" << report.truth_valid_rows << '\n';
    out << "covered_valid_rows=" << report.covered_valid_rows << '\n';
    out << "comparable_samples=" << report.comparable_samples << '\n';
    out << "valid_coverage="
        << (report.valid_coverage_available ? fixed6(report.valid_coverage) : "unavailable") << '\n';
    out << "false_valid_count=" << report.false_valid_count << '\n';
    out << "missed_valid_count=" << report.missed_valid_count << '\n';
    out << "valid_truth_missing=" << report.valid_truth_missing << '\n';
    out << "missing_truth_records=" << report.missing_truth_records << '\n';
    out << "unmatched_estimate_records=" << report.unmatched_estimate_records << '\n';
    out << "duplicate_timestamp_values=" << report.duplicate_timestamp_values << '\n';
    out << "displacement_error=" << (report.displacement_available ? "available" : "unavailable")
        << '\n';
    out << "mean_displacement_error_px="
        << (report.displacement_available ? fixed6(report.mean_displacement_error_px)
                                          : "unavailable")
        << '\n';
    out << "max_displacement_error_px="
        << (report.displacement_available ? fixed6(report.max_displacement_error_px) : "unavailable")
        << '\n';
    out << "tolerance_px=" << fixed6(report.tolerance_px) << '\n';
    out << "displacement_success=" << tri_state(report.displacement_success) << '\n';
    out << "rejection_success=" << tri_state(report.rejection_success) << '\n';
    out << "overall_success=" << (report.overall_success ? 1 : 0) << '\n';
    out << "notes=" << report.notes << '\n';
    return out.str();
}

}  // namespace quadsim
