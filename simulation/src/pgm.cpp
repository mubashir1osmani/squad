#include "quadsim/pgm.hpp"

#include <cctype>
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace quadsim {
namespace {

constexpr int kMaxDimension = 1024;

class HeaderReader {
public:
    explicit HeaderReader(std::istream& input) : input_(input) {}

    bool next_token(std::string& token) {
        token.clear();
        while (true) {
            const int c = input_.get();
            if (c == EOF) {
                return !token.empty();
            }
            if (c == '#') {
                while (true) {
                    const int n = input_.get();
                    if (n == EOF || n == '\n') {
                        break;
                    }
                }
                continue;
            }
            if (std::isspace(c) != 0) {
                if (!token.empty()) {
                    return true;
                }
                continue;
            }
            token.push_back(static_cast<char>(c));
        }
    }

private:
    std::istream& input_;
};

int parse_positive_int(const std::string& token) {
    if (token.empty() || token[0] == '0' || token[0] == '+' || token[0] == '-') {
        throw std::runtime_error("PGM dimension is not a positive integer");
    }
    int value = 0;
    for (char c : token) {
        if (c < '0' || c > '9') {
            throw std::runtime_error("PGM dimension is not a positive integer");
        }
        const int digit = c - '0';
        if (value > (kMaxDimension - digit) / 10) {
            throw std::runtime_error("PGM dimension exceeds the supported limit");
        }
        value = value * 10 + digit;
    }
    if (value < 1 || value > kMaxDimension) {
        throw std::runtime_error("PGM dimension exceeds the supported limit");
    }
    return value;
}

}  // namespace

void write_pgm(const std::string& path, const Image& image, const std::string& comment) {
    if (image.width < 1 || image.height < 1 ||
        image.pixels.size() != static_cast<std::size_t>(image.width * image.height)) {
        throw std::runtime_error("PGM image dimensions do not match the raster");
    }
    std::string safe_comment = comment;
    for (char& c : safe_comment) {
        if (c == '\n' || c == '\r') {
            c = ' ';
        }
    }
    std::ofstream out(path, std::ios::binary);
    if (!out) {
        throw std::runtime_error("failed to open PGM for writing: " + path);
    }
    out << "P5\n# " << safe_comment << '\n' << image.width << ' ' << image.height << "\n255\n";
    out.write(reinterpret_cast<const char*>(image.pixels.data()),
              static_cast<std::streamsize>(image.pixels.size()));
    if (!out) {
        throw std::runtime_error("failed to write PGM: " + path);
    }
}

Image read_pgm(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("failed to open PGM: " + path);
    }
    HeaderReader header(in);
    std::string magic;
    std::string width_token;
    std::string height_token;
    std::string maxval_token;
    if (!header.next_token(magic) || magic != "P5") {
        throw std::runtime_error("PGM magic must be P5: " + path);
    }
    if (!header.next_token(width_token) || !header.next_token(height_token) ||
        !header.next_token(maxval_token)) {
        throw std::runtime_error("PGM header is incomplete: " + path);
    }
    if (maxval_token != "255") {
        throw std::runtime_error("PGM maxval must be 255: " + path);
    }
    Image image;
    image.width = parse_positive_int(width_token);
    image.height = parse_positive_int(height_token);
    const std::size_t count =
        static_cast<std::size_t>(image.width) * static_cast<std::size_t>(image.height);
    image.pixels.resize(count);
    in.read(reinterpret_cast<char*>(image.pixels.data()), static_cast<std::streamsize>(count));
    if (static_cast<std::size_t>(in.gcount()) != count) {
        throw std::runtime_error("PGM raster is truncated: " + path);
    }
    char extra = 0;
    if (in.read(&extra, 1)) {
        throw std::runtime_error("PGM raster has trailing bytes: " + path);
    }
    return image;
}

}  // namespace quadsim
