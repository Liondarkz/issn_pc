// SPDX-License-Identifier: GPL-2.0-or-later
// port: inFAMOUS draw distance and clip planes (src/view_hook.h). The probe verifies the eboot,
// installs the two code hooks and calls issn_view_attach; a thread then keeps the game's data
// (LOD table, band distances, default clip planes) equal to the menu's values, so changes are
// live and survive the game rewriting them (debug LOD modes, script resets).
#include "issn_settings.h"
#include "issn_view.h"

#include "../../src/view_hook.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

namespace {

unsigned char* image = nullptr;
// Read by the hook stubs: {LOD_ADJUST weight, 1.0f} and 1 / FADE_DISTANCE scale.
alignas(16) float region_params[2] = {1.0f, 1.0f};
alignas(16) float fade_inverse = 1.0f;

// issn-axy: performance mode, axylpup's 1.1 SHADPS4_PERF_MODE (his common/memory_patcher.cpp
// ApplyPerfMode; +13% / +19% FPS in the heaviest city view on his PC). The game's quality ladder
// row 0 is what normal play uses: its LOD scale capped (0.7 / 0.5, the draw distance setting
// still multiplies) and one sun-shadow cascade less; the low-resolution particle pass forced
// (half / quarter); fewer high-resolution texture blocks (2 / 0 of 4); "office" also halves the
// pedestrians. ISSN_PERF_MODE=low or office (launcher: Performance > Performance mode). Every
// code site is compared first (the 01.07 eboot), all or nothing.
int perf_mode = 0; // 0 off, 1 low, 2 office

int WantedPerfMode() {
    const char* v = std::getenv("ISSN_PERF_MODE");
    if (!v || !*v) {
        v = std::getenv("SHADPS4_PERF_MODE");
    }
    const std::string mode = v ? v : "";
    return mode == "low" || mode == "1" ? 1 : mode == "office" || mode == "2" ? 2 : 0;
}

struct PerfSite {
    uint32_t offset;
    std::vector<unsigned char> original, value;
    const char* what;
};

void ApplyPerfCode(int mode) {
    const unsigned char particle_level = mode == 1 ? 1 : 2; // resolution 1.0 / 0.5 / 0.25
    const unsigned char hires_slots = mode == 1 ? 2 : 0;
    std::vector<PerfSite> sites = {
        // FUN_8003ed6a0: je over the "set level" store -> 2 nops, and the stored level.
        {0x3ed6b0, {0x74, 0x0f}, {0x90, 0x90}, "particle level: always set"},
        {0x3ed6b8, {0x00, 0x00, 0x00, 0x00}, {particle_level, 0x00, 0x00, 0x00}, "particle level"},
        // FUN_8000fd890: mov r12d, 4 / cmp eax, 4 (the 4 nearest blocks get high-res textures).
        {0xfe9a2, {0x04}, {hires_slots}, "high-res texture slots (mov)"},
        {0xfe9b2, {0x04}, {hires_slots}, "high-res texture slots (cmp)"},
        // Row 0's sun-shadow cascade offset (int, 0 in the shipped table).
        {ISSN_VIEW_LOD_TABLE + 4, {0x00, 0x00, 0x00, 0x00}, {0xff, 0xff, 0xff, 0xff},
         "sun-shadow cascades"},
    };
    if (mode == 2) {
        // Pedestrians per class = level count x ratio[world state]: 1.0 -> 0.5.
        sites.push_back({0x40ca01, {0x00, 0x00, 0x80, 0x3f}, {0x00, 0x00, 0x00, 0x3f},
                         "pedestrian ratio 0"});
        sites.push_back({0x40c83f, {0x00, 0x00, 0x80, 0x3f}, {0x00, 0x00, 0x00, 0x3f},
                         "pedestrian ratio 1"});
    }
    for (const auto& s : sites) {
        if (std::memcmp(image + s.offset, s.original.data(), s.original.size()) != 0) {
            std::printf("Performance mode: unavailable (%s differs in this eboot.bin)\n", s.what);
            return;
        }
    }
    for (const auto& s : sites) {
        std::memcpy(image + s.offset, s.value.data(), s.value.size());
    }
    perf_mode = mode;
    std::printf("Performance mode: %s (LOD cap %.1f, one sun-shadow cascade less, particles at %s "
                "resolution, %u high-res texture blocks%s)\n",
                mode == 1 ? "low" : "office", mode == 1 ? 0.7 : 0.5, mode == 1 ? "half" : "quarter",
                unsigned(hires_slots), mode == 2 ? ", half the pedestrians" : "");
}

void Put(uint32_t at, float value) {
    float current;
    std::memcpy(&current, image + at, 4);
    if (current != value) {
        std::memcpy(image + at, &value, 4);
    }
}

void Apply() {
    const auto& s = IssnSettings::Get();
    const float scale = IssnView::ClampLodScale(s.lod_scale);
    for (unsigned r = 0; r < ISSN_VIEW_LOD_ROWS; ++r) {
        float lod = issn_view_lod_original[r];
        if (perf_mode != 0) {
            lod = std::min(lod, perf_mode == 1 ? 0.7f : 0.5f);
        }
        Put(ISSN_VIEW_LOD_TABLE + r * ISSN_VIEW_LOD_STRIDE, lod * scale);
    }
    for (unsigned b = 0; b < ISSN_VIEW_BAND_COUNT; ++b) {
        Put(ISSN_VIEW_BANDS + 4 * b, IssnView::ClampBand(s.lod_bands[b]));
    }
    Put(ISSN_VIEW_NEAR_CLIP, IssnView::ClampNearClip(s.near_clip));
    Put(ISSN_VIEW_FAR_CLIP, IssnView::ClampFarClip(s.far_clip));
    region_params[0] = IssnView::ClampRegionLod(s.region_lod);
    fade_inverse = 1.0f / IssnView::ClampRegionFade(s.region_fade);
}

} // namespace

// Field of view (src/fov_hook.h): the float the camera's four sites read, in a read-write page
// next to the image; 0.5 x the menu's factor. A thread of its own (the draw distance one exists
// only when that hook is in place).
namespace {
float* fov_value = nullptr;

void ApplyFov() {
    const float value = 0.5f * IssnView::ClampFov(IssnSettings::Get().fov);
    if (*fov_value != value) {
        *fov_value = value;
    }
}
} // namespace

extern "C" void issn_fov_attach(float* value) {
    fov_value = value;
    ApplyFov(); // before the game's first frame
    IssnSettings::Get().fov_supported = true;
    std::printf("Field of view: menu control ready (x%.2f)\n", IssnSettings::Get().fov.load());
    std::thread([] {
        for (;;) {
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
            ApplyFov();
        }
    }).detach();
}

extern "C" uintptr_t issn_view_region_params(void) {
    return reinterpret_cast<uintptr_t>(region_params);
}

extern "C" uintptr_t issn_view_fade_inverse(void) {
    return reinterpret_cast<uintptr_t>(&fade_inverse);
}

extern "C" void issn_view_attach(unsigned char* guest_image) {
    image = guest_image;
    if (const int mode = WantedPerfMode(); mode != 0) {
        ApplyPerfCode(mode); // before the game's first frame, while the code is writable
    }
    Apply(); // before the game's first frame
    const auto& s = IssnSettings::Get();
    std::printf("Draw distance: LOD scale %.2f, bands %.0f/%.0f/%.0f/%.0f/%.0f/%.0f, region LOD_ADJUST weight %.2f, "
                "FADE_DISTANCE x%.2f, near %.1f, far %.0f, texture LOD bias %+.2f\n",
                s.lod_scale.load(), s.lod_bands[0].load(), s.lod_bands[1].load(), s.lod_bands[2].load(),
                s.lod_bands[3].load(), s.lod_bands[4].load(), s.lod_bands[5].load(), s.region_lod.load(),
                s.region_fade.load(), s.near_clip.load(), s.far_clip.load(), s.mip_lod_bias.load());
    IssnSettings::Get().view_supported = true;
    std::thread([] {
        for (;;) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
            Apply();
        }
    }).detach();
}
