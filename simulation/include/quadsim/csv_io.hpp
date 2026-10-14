#pragma once

#include "quadsim/types.hpp"

#include <string>
#include <vector>

namespace quadsim {

std::string format_decimal(double value);
std::string format_commands(const std::vector<CommandRow>& rows);
std::string format_flow(const std::vector<FlowRow>& rows);
std::string format_manifest(const std::vector<ManifestRow>& rows);

CommandParse parse_commands(const std::string& text);
FlowParse parse_flow(const std::string& text);
ManifestParse parse_manifest(const std::string& text);

// Gaps strictly larger than timeout_us. Schema-valid streams can still be stale.
std::vector<TimingGap> command_timeout_gaps(const std::vector<CommandRow>& rows,
                                            std::uint64_t timeout_us);

// True when a manifest filename is absolute or escapes its directory.
bool manifest_filename_unsafe(const std::string& filename);

}  // namespace quadsim
