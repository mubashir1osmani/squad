#include "quad_vision/reference.hpp"

#include "quad_vision/constants.hpp"

#include <stddef.h>
#include <stdint.h>

#include <vector>

// Independent host reference for the same block-match contract as kernel.cpp.
// Heap use is intentional so tests can tell this file from the HLS candidate.

namespace quad_vision {
namespace {

const uint8_t* row_ptr(const uint8_t* img, int stride, int y) {
  return img + static_cast<size_t>(y) * static_cast<size_t>(stride);
}

int64_t window_sum(const uint8_t* img, int stride, int x, int y) {
  int64_t sum = 0;
  for (int row = 0; row < kBlockSize; ++row) {
    const uint8_t* p = row_ptr(img, stride, y + row) + x;
    for (int col = 0; col < kBlockSize; ++col) {
      sum += p[col];
    }
  }
  return sum;
}

int64_t texture_energy(const uint8_t* img, int stride, int x, int y, int64_t sum) {
  int64_t energy = 0;
  for (int row = 0; row < kBlockSize; ++row) {
    const uint8_t* p = row_ptr(img, stride, y + row) + x;
    for (int col = 0; col < kBlockSize; ++col) {
      const int64_t centered = static_cast<int64_t>(kArea) * p[col] - sum;
      energy += centered < 0 ? -centered : centered;
    }
  }
  return energy;
}

int64_t zsad(const uint8_t* prev,
             int prev_stride,
             int px,
             int py,
             int64_t sum_prev,
             const uint8_t* curr,
             int curr_stride,
             int cx,
             int cy) {
  const int64_t sum_curr = window_sum(curr, curr_stride, cx, cy);
  int64_t score = 0;
  for (int row = 0; row < kBlockSize; ++row) {
    const uint8_t* pr = row_ptr(prev, prev_stride, py + row) + px;
    const uint8_t* cr = row_ptr(curr, curr_stride, cy + row) + cx;
    for (int col = 0; col < kBlockSize; ++col) {
      const int64_t dp = static_cast<int64_t>(kArea) * pr[col] - sum_prev;
      const int64_t dc = static_cast<int64_t>(kArea) * cr[col] - sum_curr;
      const int64_t diff = dp - dc;
      score += diff < 0 ? -diff : diff;
    }
  }
  return score;
}

int collect_origins(int length, std::vector<int>& origins) {
  origins.clear();
  for (int origin = kSearchRadius; origin + kBlockSize + kSearchRadius <= length;
       origin += kBlockSize) {
    if (static_cast<int>(origins.size()) >= kMaxBlocksPerAxis) {
      break;
    }
    origins.push_back(origin);
  }
  return static_cast<int>(origins.size());
}

void clear_estimate(FlowEstimate* out) {
  out->dx_px = 0;
  out->dy_px = 0;
  out->quality = 0;
  out->valid = 0;
}

}  // namespace

KernelStatus estimate_reference(const uint8_t* prev,
                                const uint8_t* curr,
                                int width,
                                int height,
                                int prev_stride,
                                int curr_stride,
                                FlowEstimate* out) {
  if (out == nullptr) {
    return KernelStatus::BadArgument;
  }
  clear_estimate(out);
  if (prev == nullptr || curr == nullptr) {
    return KernelStatus::BadArgument;
  }
  if (width < kMinDimension || height < kMinDimension || width > kMaxDimension ||
      height > kMaxDimension) {
    return KernelStatus::UnsupportedSize;
  }
  if (prev_stride < width || curr_stride < width || prev_stride > kMaxStride ||
      curr_stride > kMaxStride) {
    return KernelStatus::BadArgument;
  }

  std::vector<int> x_origins;
  std::vector<int> y_origins;
  const int nx = collect_origins(width, x_origins);
  const int ny = collect_origins(height, y_origins);
  const int placed = nx * ny;
  if (nx <= 0 || ny <= 0 || placed <= 0) {
    return KernelStatus::Ok;
  }

  struct Vote {
    int dx;
    int dy;
  };
  std::vector<Vote> votes;
  votes.reserve(static_cast<size_t>(placed));

  for (int iy = 0; iy < ny; ++iy) {
    for (int ix = 0; ix < nx; ++ix) {
      const int bx = x_origins[static_cast<size_t>(ix)];
      const int by = y_origins[static_cast<size_t>(iy)];
      const int64_t sum_prev = window_sum(prev, prev_stride, bx, by);
      const int64_t energy = texture_energy(prev, prev_stride, bx, by, sum_prev);
      if (energy < kTextureEnergyMin) {
        continue;
      }

      std::vector<int64_t> scores(static_cast<size_t>(kSearchWindow * kSearchWindow));
      for (int oy = -kSearchRadius; oy <= kSearchRadius; ++oy) {
        for (int ox = -kSearchRadius; ox <= kSearchRadius; ++ox) {
          const size_t index = static_cast<size_t>(oy + kSearchRadius) *
                                   static_cast<size_t>(kSearchWindow) +
                               static_cast<size_t>(ox + kSearchRadius);
          scores[index] = zsad(prev, prev_stride, bx, by, sum_prev, curr, curr_stride,
                               bx + ox, by + oy);
        }
      }

      int best_ox = -kSearchRadius;
      int best_oy = -kSearchRadius;
      int64_t best_score = scores[0];
      for (int oy = -kSearchRadius; oy <= kSearchRadius; ++oy) {
        for (int ox = -kSearchRadius; ox <= kSearchRadius; ++ox) {
          const size_t index = static_cast<size_t>(oy + kSearchRadius) *
                                   static_cast<size_t>(kSearchWindow) +
                               static_cast<size_t>(ox + kSearchRadius);
          const int64_t score = scores[index];
          if (score < best_score) {
            best_score = score;
            best_ox = ox;
            best_oy = oy;
          }
        }
      }

      bool have_second = false;
      int64_t second_score = 0;
      for (int oy = -kSearchRadius; oy <= kSearchRadius; ++oy) {
        for (int ox = -kSearchRadius; ox <= kSearchRadius; ++ox) {
          const int adx = ox > best_ox ? ox - best_ox : best_ox - ox;
          const int ady = oy > best_oy ? oy - best_oy : best_oy - oy;
          const int chebyshev = adx > ady ? adx : ady;
          if (chebyshev < kExclusionChebyshev) {
            continue;
          }
          const size_t index = static_cast<size_t>(oy + kSearchRadius) *
                                   static_cast<size_t>(kSearchWindow) +
                               static_cast<size_t>(ox + kSearchRadius);
          const int64_t score = scores[index];
          if (!have_second || score < second_score) {
            have_second = true;
            second_score = score;
          }
        }
      }

      const bool unambiguous = have_second && second_score > best_score &&
                               second_score * kSecondVsBestDen >=
                                   best_score * static_cast<int64_t>(kSecondVsBestNum);
      if (!unambiguous) {
        continue;
      }

      const bool on_boundary = best_ox == kSearchRadius || best_ox == -kSearchRadius ||
                               best_oy == kSearchRadius || best_oy == -kSearchRadius;
      if (on_boundary && best_score != 0) {
        continue;
      }

      votes.push_back(Vote{best_ox, best_oy});
    }
  }

  struct Bucket {
    int dx;
    int dy;
    int count;
  };
  std::vector<Bucket> buckets;
  for (const Vote& vote : votes) {
    int found = -1;
    for (int b = 0; b < static_cast<int>(buckets.size()); ++b) {
      if (buckets[static_cast<size_t>(b)].dx == vote.dx &&
          buckets[static_cast<size_t>(b)].dy == vote.dy) {
        found = b;
        break;
      }
    }
    if (found < 0) {
      buckets.push_back(Bucket{vote.dx, vote.dy, 1});
    } else {
      ++buckets[static_cast<size_t>(found)].count;
    }
  }

  int best_bucket = -1;
  bool unique = false;
  for (int b = 0; b < static_cast<int>(buckets.size()); ++b) {
    if (best_bucket < 0 ||
        buckets[static_cast<size_t>(b)].count > buckets[static_cast<size_t>(best_bucket)].count) {
      best_bucket = b;
      unique = true;
    } else if (buckets[static_cast<size_t>(b)].count ==
               buckets[static_cast<size_t>(best_bucket)].count) {
      unique = false;
    }
  }

  if (best_bucket < 0 || !unique) {
    return KernelStatus::Ok;
  }
  const int mode_count = buckets[static_cast<size_t>(best_bucket)].count;
  const int vote_count = static_cast<int>(votes.size());
  if (!(mode_count * 2 > vote_count && mode_count * 2 >= placed)) {
    return KernelStatus::Ok;
  }

  int quality = (255 * mode_count) / placed;
  if (quality < 1) {
    quality = 1;
  }
  if (quality > 255) {
    quality = 255;
  }
  out->dx_px = buckets[static_cast<size_t>(best_bucket)].dx;
  out->dy_px = buckets[static_cast<size_t>(best_bucket)].dy;
  out->quality = quality;
  out->valid = 1;
  return KernelStatus::Ok;
}

}  // namespace quad_vision
