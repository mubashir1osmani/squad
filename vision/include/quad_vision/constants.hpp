#pragma once

// Numeric contract for the host reference and the allocation-free kernel.
// Both must use these values. Pixel displacements are integers in the image
// plane (+x right, +y down), not metres or body rates.

namespace quad_vision {

constexpr int kBlockSize = 16;
constexpr int kSearchRadius = 8;
constexpr int kMinDimension = kBlockSize + 2 * kSearchRadius;  // 32
constexpr int kMaxDimension = 128;
constexpr int kArea = kBlockSize * kBlockSize;  // 256
constexpr int kMinMeanAbsDev = 8;
// sum |area * pixel - block_sum| >= kTextureEnergyMin means MAD >= 8.
constexpr int kTextureEnergyMin = kMinMeanAbsDev * kArea * kArea;  // 524288
constexpr int kSearchWindow = kSearchRadius * 2 + 1;               // 17
constexpr int kMaxOrigins =
    (kMaxDimension - kBlockSize - 2 * kSearchRadius) / kBlockSize + 1;  // 7
constexpr int kMaxBlocksPerAxis = 8;
constexpr int kExclusionChebyshev = 2;
// Unambiguous when second > best and 10 * second >= 11 * best.
constexpr int kSecondVsBestNum = 11;
constexpr int kSecondVsBestDen = 10;
constexpr int kMaxFrames = 128;
constexpr int kMaxStride = 4096;
constexpr int kMaxPgmFileBytes = kMaxDimension * kMaxDimension + 4096;
constexpr int kMaxManifestBytes = 256 * 1024;
constexpr int kMaxLineLength = 1024;
constexpr int kMaxFilenameLength = 240;

static_assert(kMinDimension == 32, "minimum frame edge");
static_assert(kMaxDimension == 128, "maximum frame edge");
static_assert(kTextureEnergyMin == 524288, "texture gate");
static_assert(kMaxOrigins == 7, "origin count at the maximum frame");
static_assert(kMaxOrigins <= kMaxBlocksPerAxis, "fixed vote table covers the grid");
static_assert(kSearchRadius >= 1, "search");
static_assert(kSecondVsBestDen > 0, "ambiguity ratio");

}  // namespace quad_vision
