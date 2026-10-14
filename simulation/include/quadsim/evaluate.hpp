#pragma once

#include "quadsim/types.hpp"

#include <string>

namespace quadsim {

// Tri-state flags use -1 for not_applicable, 0 for false, and 1 for true.
struct EvalReport {
    bool inputs_well_formed = false;
    std::string schema_error;
    int truth_rows = 0;
    int estimate_rows = 0;
    int matched_rows = 0;
    int truth_valid_rows = 0;
    int covered_valid_rows = 0;
    int comparable_samples = 0;
    int false_valid_count = 0;
    int missed_valid_count = 0;
    int valid_truth_missing = 0;
    int missing_truth_records = 0;
    int unmatched_estimate_records = 0;
    int duplicate_timestamp_values = 0;
    bool displacement_available = false;
    bool valid_coverage_available = false;
    double valid_coverage = 0;
    double mean_displacement_error_px = 0;
    double max_displacement_error_px = 0;
    double tolerance_px = 0;
    int displacement_success = -1;
    int rejection_success = -1;
    bool overall_success = false;
    std::string notes;
};

EvalReport evaluate_flow(const std::vector<FlowRow>& truth,
                         const std::vector<FlowRow>& estimate,
                         double tolerance_px);

// Parses both files. A schema failure leaves displacement unavailable.
EvalReport evaluate_flow_text(const std::string& truth_text,
                              const std::string& estimate_text,
                              double tolerance_px);

std::string format_evaluation(const EvalReport& report);

}  // namespace quadsim
