#include "quad_vision/manifest.hpp"

#include "quad_vision/constants.hpp"

#include <fstream>
#include <sstream>

namespace quad_vision {
namespace {

ManifestResult fail(const std::string& error) {
  ManifestResult result;
  result.ok = false;
  result.error = error;
  return result;
}

bool parse_u64(const std::string& text, std::uint64_t& value) {
  if (text.empty() || text.size() > 20) {
    return false;
  }
  std::uint64_t acc = 0;
  for (char c : text) {
    if (c < '0' || c > '9') {
      return false;
    }
    const std::uint64_t digit = static_cast<std::uint64_t>(c - '0');
    if (acc > (static_cast<std::uint64_t>(-1) - digit) / 10) {
      return false;
    }
    acc = acc * 10 + digit;
  }
  value = acc;
  return true;
}

}  // namespace

bool filename_is_safe(const std::string& filename, std::string& error) {
  if (filename.empty()) {
    error = "manifest: unsafe filename (empty)";
    return false;
  }
  if (filename.size() > static_cast<std::size_t>(kMaxFilenameLength)) {
    error = "manifest: unsafe filename (too long)";
    return false;
  }
  if (filename.front() == '/' || filename.front() == '\\') {
    error = "manifest: unsafe filename (absolute path)";
    return false;
  }
  for (unsigned char c : filename) {
    if (c < 0x20 || c == 0x7f) {
      error = "manifest: unsafe filename (control character)";
      return false;
    }
  }
  if (filename.find('\\') != std::string::npos) {
    error = "manifest: unsafe filename (backslash)";
    return false;
  }
  if (filename.find(':') != std::string::npos) {
    error = "manifest: unsafe filename (colon)";
    return false;
  }
  if (filename.find(' ') != std::string::npos) {
    error = "manifest: unsafe filename (space)";
    return false;
  }

  std::size_t begin = 0;
  while (begin <= filename.size()) {
    const std::size_t slash = filename.find('/', begin);
    const std::size_t end = slash == std::string::npos ? filename.size() : slash;
    const std::string segment = filename.substr(begin, end - begin);
    if (segment.empty()) {
      error = "manifest: unsafe filename (empty segment)";
      return false;
    }
    if (segment == "..") {
      error = "manifest: unsafe filename (parent traversal)";
      return false;
    }
    if (slash == std::string::npos) {
      break;
    }
    begin = slash + 1;
    if (begin == filename.size()) {
      error = "manifest: unsafe filename (empty segment)";
      return false;
    }
  }
  return true;
}

ManifestResult read_manifest(const std::string& path) {
  std::ifstream in(path, std::ios::binary);
  if (!in) {
    return fail("manifest: could not open file");
  }
  in.seekg(0, std::ios::end);
  const std::streamoff end = in.tellg();
  if (end < 0) {
    return fail("manifest: could not read file");
  }
  if (end > kMaxManifestBytes) {
    return fail("manifest: file is too large");
  }
  in.seekg(0, std::ios::beg);
  std::string bytes(static_cast<std::size_t>(end), '\0');
  if (end > 0 && !in.read(&bytes[0], end)) {
    return fail("manifest: could not read file");
  }
  if (bytes.find('\0') != std::string::npos) {
    return fail("manifest: malformed file");
  }

  std::string directory = ".";
  const std::size_t slash = path.find_last_of("/\\");
  if (slash != std::string::npos) {
    directory = path.substr(0, slash);
    if (directory.empty()) {
      directory = "/";
    }
  }

  ManifestResult result;
  result.manifest.directory = directory;

  std::size_t pos = 0;
  bool saw_header = false;
  std::uint64_t previous = 0;
  bool have_previous = false;

  while (pos < bytes.size()) {
    std::size_t eol = bytes.find('\n', pos);
    if (eol == std::string::npos) {
      eol = bytes.size();
    }
    std::string line = bytes.substr(pos, eol - pos);
    pos = eol < bytes.size() ? eol + 1 : bytes.size();
    if (!line.empty() && line.back() == '\r') {
      line.pop_back();
    }
    if (line.empty()) {
      return fail("manifest: empty line");
    }
    if (line.size() > static_cast<std::size_t>(kMaxLineLength)) {
      return fail("manifest: line is too long");
    }
    if (line.find('\r') != std::string::npos) {
      return fail("manifest: malformed line");
    }

    if (!saw_header) {
      if (line != "t_us,filename") {
        return fail("manifest: expected header t_us,filename");
      }
      saw_header = true;
      continue;
    }

    const std::size_t comma = line.find(',');
    if (comma == std::string::npos || line.find(',', comma + 1) != std::string::npos) {
      return fail("manifest: expected two fields");
    }
    const std::string time_text = line.substr(0, comma);
    const std::string filename = line.substr(comma + 1);
    std::uint64_t timestamp = 0;
    if (!parse_u64(time_text, timestamp)) {
      return fail("manifest: malformed timestamp");
    }
    if (have_previous && timestamp <= previous) {
      return fail("manifest: timestamps must be strictly increasing");
    }
    std::string path_error;
    if (!filename_is_safe(filename, path_error)) {
      return fail(path_error);
    }
    if (static_cast<int>(result.manifest.frames.size()) >= kMaxFrames) {
      return fail("manifest: too many frames");
    }
    ManifestFrame frame;
    frame.t_us = timestamp;
    frame.filename = filename;
    result.manifest.frames.push_back(frame);
    previous = timestamp;
    have_previous = true;
  }

  if (!saw_header) {
    return fail("manifest: expected header t_us,filename");
  }
  if (result.manifest.frames.size() < 2) {
    return fail("manifest: need at least two frames");
  }
  result.ok = true;
  return result;
}

}  // namespace quad_vision
