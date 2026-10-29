// SPDX-FileCopyrightText: Copyright 2026 axylpup (shadPS4 second-son build); port glue issn_pc
// SPDX-License-Identifier: GPL-2.0-or-later
// issn-axy: axylpup's inFAMOUS Second Son code patches (src/common/memory_patcher.cpp of
// github.com/axylpup/shadPS4, branch second-son, 2212a345), applied by the port's loader to the
// linked image before its pages are protected. Each one compares every original byte first and
// is refused on any difference, so a patch from the port's own files that touched the same code
// simply turns it off. Flags as in axylpup's release (shadps4-flags.txt):
//   SHADPS4_PARTICLE_LATE_READ=1, SHADPS4_PARTICLE_ARENA_DISCARD=1 (on), and the optional
//   SHADPS4_PARTICLE_LIGHT_ONE_SUBMIT, SHADPS4_HOUSE_SPLIT / SHADPS4_HOUSE_CHUNK (off).
#include <cstdio>
#include <cmath>
#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <fmt/format.h>
#include "../issngpu.h"
#include "common/logging/log.h"
#include "common/memory_patcher.h"
#include "common/types.h"
#include "video_core/renderer_vulkan/vk_dlss.h"

namespace VideoCore {
void PS4_SYSV_ABI ParticleArenaDiscardHook(); // buffer_cache.cpp
}
// issn_pc: the screen shape (issngpu.cpp: window size; MemoryPatcher::IsRenderShaped / MovieFitRect).
namespace IssnShape {
bool ScreenPixels(int& width, int& height);
extern double g_aspect;
extern float g_movie_x, g_movie_y;
} // namespace IssnShape

namespace {
using MemoryPatcher::g_eboot_address;
bool g_particle_late_read_applied = false; // V1 written (ApplyParticleLightOneSubmit needs it)
// SHADPS4_PARTICLE_LATE_READ=1 (2026-10-12, notes/one-frame-late-particles-2026-10-12.md sec. 3.1, V1):
// inFAMOUS Second Son (CUSA00004 / 01.07) reads its particle control blocks (survivor counts, light
// counts, CPU collision) at the start of the next frame, just before 0x3ee7d0 consumes them, instead
// of right after kicking this frame's particle chain. Game:Main then no longer waits for that chain's
// GPU work mid-frame. Three writes (eboot offsets): a cave in the unreferenced 0x3ee160 that calls
// 0x3edef0 (readback) and 0x3ee470 (collision join + label wait), then sets up 0x3ee7d0's arguments
// and jumps back; the main loop jumps to it at 0x29cc89; the old 0x3edef0 call at 0x29cd1e becomes a
// 5-byte NOP. The end-of-frame 0x3ee470 call at 0x29cd56 stays. Refused unless every byte matches.
static void ApplyParticleLateRead() {
    const char* env = std::getenv("SHADPS4_PARTICLE_LATE_READ");
    if (!env || env[0] != '1') {
        return;
    }
    // No serial / APP_VER check (2026-10-19): a DLC param.sfo in a player's game folder read as
    // "Unknown version" and switched this off; the byte compare below is the gate.
    struct Bytes {
        u64 offset;
        std::vector<u8> bytes;
    };
    // Original bytes: the patched ranges plus the instructions around them the cave relies on.
    const std::vector<Bytes> original = {
        // FUN_8003ee160 (no references): 43 code bytes + 5 pad
        {0x3ee160, {0x55, 0x48, 0x89, 0xe5, 0x53, 0x50, 0x48, 0x8b, 0x5f, 0x58, 0xeb, 0x13,
                    0x0f, 0x1f, 0x40, 0x00, 0x48, 0x89, 0xdf, 0xe8, 0x58, 0x5d, 0x00, 0x00,
                    0x48, 0x8b, 0x9b, 0xb0, 0x00, 0x00, 0x00, 0x48, 0x85, 0xdb, 0x75, 0xec,
                    0x48, 0x83, 0xc4, 0x08, 0x5b, 0x5d, 0xc3, 0x90, 0x90, 0x90, 0x90, 0x90}},
        // call 0x41b8d0; lea rax,[0x800f1bcd0]; lea rdi,[0x807d3eb10]; mov rsi,[rax+0x1dca0];
        // call 0x3ee7d0
        {0x29cc84, {0xe8, 0x47, 0xec, 0x17, 0x00, 0x48, 0x8d, 0x05, 0x40, 0xf0, 0xc7,
                    0x00, 0x48, 0x8d, 0x3d, 0x79, 0x1e, 0xaa, 0x07, 0x48, 0x8b, 0xb0,
                    0xa0, 0xdc, 0x01, 0x00, 0xe8, 0x2d, 0x1b, 0x15, 0x00}},
        // lea rbx,[0x807d3eb10]; mov rdi,rbx; call 0x3edef0
        {0x29cd14, {0x48, 0x8d, 0x1d, 0xf5, 0x1d, 0xaa, 0x07, 0x48, 0x89, 0xdf, 0xe8, 0xcd, 0x11,
                    0x15, 0x00}},
        // mov rdi,rbx; call 0x3ee470 (kept)
        {0x29cd53, {0x48, 0x89, 0xdf, 0xe8, 0x15, 0x17, 0x15, 0x00}},
    };
    const std::vector<Bytes> patched = {
        // lea rbx,[0x807d3eb10]; mov rdi,rbx; call 0x3edef0; mov rdi,rbx; call 0x3ee470;
        // lea rax,[0x800f1bcd0]; mov rdi,rbx; mov rsi,[rax+0x1dca0]; jmp 0x29cc9e
        {0x3ee160, {0x48, 0x8d, 0x1d, 0xa9, 0x09, 0x95, 0x07, 0x48, 0x89, 0xdf, 0xe8, 0x81,
                    0xfd, 0xff, 0xff, 0x48, 0x89, 0xdf, 0xe8, 0xf9, 0x02, 0x00, 0x00, 0x48,
                    0x8d, 0x05, 0x52, 0xdb, 0xb2, 0x00, 0x48, 0x89, 0xdf, 0x48, 0x8b, 0xb0,
                    0xa0, 0xdc, 0x01, 0x00, 0xe9, 0x11, 0xeb, 0xea, 0xff}},
        // jmp 0x3ee160 + 16 NOPs up to the call 0x3ee7d0 at 0x29cc9e
        {0x29cc89, {0xe9, 0xd2, 0x14, 0x15, 0x00, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90,
                    0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90}},
        // nopl 0x0(rax,rax,1) in place of call 0x3edef0
        {0x29cd1e, {0x0f, 0x1f, 0x44, 0x00, 0x00}},
    };
    for (const auto& o : original) {
        const auto* live = reinterpret_cast<const u8*>(g_eboot_address + o.offset);
        if (std::memcmp(live, o.bytes.data(), o.bytes.size()) != 0) {
            std::string found;
            for (size_t i = 0; i < o.bytes.size(); ++i) {
                found += fmt::format("{:02x}", live[i]);
            }
            LOG_ERROR(Loader, "SHADPS4_PARTICLE_LATE_READ: refused, eboot+{:#x} holds {}", o.offset,
                      found);
            return;
        }
    }
    // SHADPS4_PARTICLE_ARENA_DISCARD=1 (2026-10-13): the main loop first calls a host hook that
    // drops the GPU-modified state of the arena half 0x3ee7d0 is about to reuse (dead data), then
    // jumps to the cave: movabs rax, hook; call rax; jmp 0x3ee160; 4 NOPs (21 bytes at 0x29cc89).
    std::vector<Bytes> writes = patched;
    const char* discard = std::getenv("SHADPS4_PARTICLE_ARENA_DISCARD");
    if (discard && discard[0] == '1') {
        const u64 hook = reinterpret_cast<u64>(&VideoCore::ParticleArenaDiscardHook);
        std::vector<u8> b = {0x48, 0xb8};
        for (int i = 0; i < 8; ++i) {
            b.push_back(static_cast<u8>(hook >> (8 * i)));
        }
        // Opus review (q43): the hook runs AFTER the cave (the cave may still read a stale
        // control block in the half being discarded): 0x29cc89 jmp cave (as V1); the cave's
        // last jmp returns to 0x29cc8e = push rdi; push rsi; movabs rax, hook; call rax;
        // pop rsi; pop rdi (16 bytes, ends at 0x29cc9e = call 0x3ee7d0).
        std::vector<u8> tail = {0x57, 0x56};
        tail.insert(tail.end(), b.begin(), b.end());
        tail.insert(tail.end(), {0xff, 0xd0, 0x5e, 0x5f});
        std::vector<u8> jump = {0xe9, 0xd2, 0x14, 0x15, 0x00};
        jump.insert(jump.end(), tail.begin(), tail.end());
        writes[1] = {0x29cc89, jump};
        writes[0].bytes[41] = 0x01; // cave: jmp 0x29cc8e (E9 01 EB EA FF) instead of 0x29cc9e
        if (writes[1].bytes.size() != 21 || writes[0].bytes[40] != 0xe9) {
            LOG_ERROR(Loader, "SHADPS4_PARTICLE_ARENA_DISCARD: bad patch layout, not applied");
            writes = patched;
        } else {
            LOG_INFO(Loader, "SHADPS4_PARTICLE_ARENA_DISCARD: frame-start hook {:#x} after the cave",
                     hook);
        }
    }
    for (const auto& p : writes) {
        std::memcpy(reinterpret_cast<void*>(g_eboot_address + p.offset), p.bytes.data(),
                    p.bytes.size());
    }
    g_particle_late_read_applied = true;
    LOG_INFO(Loader,
             "SHADPS4_PARTICLE_LATE_READ: applied V1 (particle readback one frame later): cave "
             "{:#x}, jump {:#x}, nop {:#x}",
             g_eboot_address + 0x3ee160, g_eboot_address + 0x29cc89, g_eboot_address + 0x29cd1e);
}

// SHADPS4_PARTICLE_LIGHT_ONE_SUBMIT=1 (2026-10-16, notes/study-fight-dips-2026-10-15.md sec. 5, G1):
// 0x3f3ed0 records two DMA_DATAs per light-emitting particle effect and then submits the ring
// (call 0x1691e0 at eboot 0x3f4036) - one ring submit per light, ~25 per typical fight frame, ~74 in
// the worst. With V1 (PARTICLE_LATE_READ) the copies stay in the command buffer 0x807d3cf20 and go out
// with 0x3ee7d0's first submit of the same frame, so the call becomes a 5-byte NOP. Only with V1
// applied: in the vanilla order the copies would wait for the next frame (lights one frame late).
static void ApplyParticleLightOneSubmit() {
    const char* env = std::getenv("SHADPS4_PARTICLE_LIGHT_ONE_SUBMIT");
    if (!env || env[0] != '1') {
        return;
    }
    if (!g_particle_late_read_applied) {
        LOG_ERROR(Loader, "SHADPS4_PARTICLE_LIGHT_ONE_SUBMIT: refused, needs SHADPS4_PARTICLE_LATE_READ "
                          "applied (V1)");
        return;
    }
    constexpr u64 offset = 0x3f4036;
    static constexpr u8 original[] = {0xe8, 0xa5, 0x51, 0xd7, 0xff}; // call 0x1691e0
    static constexpr u8 nop5[] = {0x0f, 0x1f, 0x44, 0x00, 0x00};
    auto* live = reinterpret_cast<u8*>(g_eboot_address + offset);
    if (std::memcmp(live, original, sizeof(original)) != 0) {
        LOG_ERROR(Loader, "SHADPS4_PARTICLE_LIGHT_ONE_SUBMIT: refused, eboot+{:#x} holds "
                          "{:02x} {:02x} {:02x} {:02x} {:02x}",
                  offset, live[0], live[1], live[2], live[3], live[4]);
        return;
    }
    std::memcpy(live, nop5, sizeof(nop5));
    LOG_INFO(Loader, "SHADPS4_PARTICLE_LIGHT_ONE_SUBMIT: per-light ring submit at {:#x} is a NOP",
             g_eboot_address + offset);
}

// SHADPS4_HOUSE_SPLIT=1 (2026-10-14, notes/study-house-draws-2026-10-14.md sec. 3): the game's
// command-buffer splitter FUN_80043cef0 cuts a pass's item list into jobs of <= 1000 items only where
// key & mask changes; the mask comes from FUN_800197130 (default 0xffff000000000000 = class runs).
// The rooftop's ~3,300 distant houses are one class run, so ONE job records them all. Writing
// 0xffffffffffff0000 (everything above bit 16: a superset of today's cut points) lets the splitter
// cut that run into ~5 jobs recorded in parallel. SHADPS4_HOUSE_CHUNK=N also sets the 1000-item chunk
// (FUN_800197050's imm32). Refused unless every original byte matches. The game's context pool has
// no bounds check (at most 254 per frame): the census in gnmdriver logs the count per frame.
static void ApplyHouseSplit() {
    const char* env = std::getenv("SHADPS4_HOUSE_SPLIT");
    const char* chunk_env = std::getenv("SHADPS4_HOUSE_CHUNK");
    // =2 (diagnostic, study 11:35): FUN_800197130 -> mov rax, -1; ret - every run > 1000 items is
    // cut at its chunk (200 in passes 0x13 / 0x17); tells apart "houses in another pass / equal keys"
    // (contexts up, span down) from "the pass is walked serially" (contexts up, span flat).
    const bool all_ones = env && env[0] == '2';
    const bool mask_on = env && (env[0] == '1' || all_ones);
    const u32 chunk = chunk_env ? static_cast<u32>(std::strtoul(chunk_env, nullptr, 10)) : 0;
    if (!mask_on && chunk == 0) {
        return;
    }
    // No serial / APP_VER check (2026-10-19, as PARTICLE_LATE_READ): the byte compare is the gate.
    // FUN_800197130 (split mask by pass) and FUN_800197050 (chunk by pass), whole functions.
    static constexpr u8 mask_fn[58] = {
        0xba, 0x01, 0x00, 0x00, 0x00, 0x40, 0x88, 0xf9, 0xb8, 0x00, 0x00, 0xff, 0x7f, 0x48, 0xbe,
        0x00, 0x00, 0x00, 0x00, 0x1f, 0x00, 0x00, 0x00, 0x48, 0xd3, 0xe2, 0x48, 0x0f, 0xa3, 0xfe,
        0x72, 0x19, 0xf7, 0xc2, 0x00, 0x44, 0x88, 0x00, 0xb9, 0x00, 0x00, 0xff, 0xff, 0x48, 0xb8,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff, 0xff, 0x48, 0x0f, 0x45, 0xc1, 0xc3};
    static constexpr u8 chunk_fn[19] = {0x83, 0xcf, 0x04, 0xb8, 0xc8, 0x00, 0x00, 0x00, 0x83, 0xff,
                                        0x17, 0x74, 0x05, 0xb8, 0xe8, 0x03, 0x00, 0x00, 0xc3};
    const auto matches = [](u64 offset, const u8* bytes, size_t n) {
        const auto* live = reinterpret_cast<const u8*>(g_eboot_address + offset);
        if (std::memcmp(live, bytes, n) == 0) {
            return true;
        }
        std::string found;
        for (size_t i = 0; i < n; ++i) {
            found += fmt::format("{:02x}", live[i]);
        }
        LOG_ERROR(Loader, "SHADPS4_HOUSE_SPLIT: refused, eboot+{:#x} holds {}", offset, found);
        return false;
    };
    if (!matches(0x197130, mask_fn, sizeof(mask_fn)) ||
        !matches(0x197050, chunk_fn, sizeof(chunk_fn))) {
        return;
    }
    if (all_ones) {
        // mov rax, -1; ret at the function start (48 c7 c0 ff ff ff ff c3)
        static constexpr u8 ret_all[8] = {0x48, 0xc7, 0xc0, 0xff, 0xff, 0xff, 0xff, 0xc3};
        std::memcpy(reinterpret_cast<void*>(g_eboot_address + 0x197130), ret_all, sizeof(ret_all));
    } else if (mask_on) {
        // imm64 of mov rax, 0xffff000000000000 at 0x19715d -> 0xffffffffffff0000
        static constexpr u8 mask[8] = {0x00, 0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
        std::memcpy(reinterpret_cast<void*>(g_eboot_address + 0x19715d), mask, sizeof(mask));
    }
    if (chunk != 0) {
        // imm32 of mov eax, 1000 at 0x19705e
        std::memcpy(reinterpret_cast<void*>(g_eboot_address + 0x19705e), &chunk, sizeof(chunk));
    }
    LOG_INFO(Loader, "SHADPS4_HOUSE_SPLIT: split mask {} | chunk {}",
             all_ones  ? "all ones in every pass (diagnostic)"
             : mask_on ? "default 0xffffffffffff0000"
                       : "unchanged",
             chunk != 0 ? chunk : 1000);
}
// issn-axy: render size (axylpup's 1.1 SHADPS4_RENDER_HEIGHT / screen percentage, his
// common/memory_patcher.cpp ApplyAspectRatio, 16:9 only, decided at boot, no live changes).
// The renderer's size setup FUN_8001eff00 stores the render size and the output size from four
// 1920 / 1080 immediates, and the mode toggle FUN_8001f0be0 re-applies them from two more; all
// six get the chosen size, so every buffer follows. The 2D screen space (HUD layout, camera
// aspect) keeps its 1920x1080 immediates: some HUD parts are sized in 1080p pixels (his s1440).
// The picture reaches the window through FSR 4 (upscaling, vk_dlss_upscale.h) or the presenter's
// scaling. ISSN_RENDER_SCALE = percent of 1080 lines (50-200), ISSN_RENDER_HEIGHT = lines.
// Above 3200x1800 the game's tile light list is enlarged (his ApplyLightListCapacity: 3840x2160
// lost the device with the game's 25,600 tiles).
static u32 Round8(double v) {
    return static_cast<u32>(std::lround(v / 8.0) * 8);
}

static void ApplyLightListCapacity(u32 width, u32 height) {
    constexpr u32 Original = 0x320000;
    const u64 tiles = u64((width + 15) / 16) * ((height + 15) / 16);
    const u64 need = tiles * 128;
    if (need <= Original) {
        return;
    }
    const u32 entries = static_cast<u32>((need + 0xffff) & ~u64(0xffff));
    const auto at = [](u64 offset) { return reinterpret_cast<u8*>(g_eboot_address + offset); };
    static constexpr u8 imul_head[3] = {0x41, 0x69, 0xf7}; // imul esi, r15d, imm32 (0x16be04)
    static constexpr u8 mov_head[2] = {0x41, 0xb8};        // mov r8d, imm32 (0x16be1d)
    u32 imul_imm = 0, mov_imm = 0;
    std::memcpy(&imul_imm, at(0x16be07), 4);
    std::memcpy(&mov_imm, at(0x16be1f), 4);
    if (std::memcmp(at(0x16be04), imul_head, 3) != 0 || std::memcmp(at(0x16be1d), mov_head, 2) != 0 ||
        imul_imm != Original || mov_imm != Original) {
        std::printf("Render size: light list not enlarged (the code differs in this eboot.bin)\n");
        return;
    }
    std::memcpy(at(0x16be07), &entries, 4);
    std::memcpy(at(0x16be1f), &entries, 4);
    std::printf("Render size: light list %u entries for %ux%u\n", entries, width, height);
}

/// The render size at 16:9-basis height h0 in the shape `aspect` (axylpup's SizeAt): wide shapes
/// keep the height (up to 32:9), narrow ones keep the 16:9 width (down to 1:1).
static void SizeAt(u32 h0, double aspect, u32& width, u32& height) {
    if (aspect < 16.0 / 9.0 - 1e-6) {
        width = Round8(h0 * 16.0 / 9.0);
        height = std::min(width, Round8(width / aspect));
    } else {
        width = std::min(Round8(h0 * 32.0 / 9.0), Round8(h0 * aspect));
        height = h0;
    }
}

// Movies fit inside the screen (axylpup 1.1.1): the full-screen movie draw FUN_8004575b0 samples
// the movie by f = (ccc4 * 16) / (ccc0 * 9) on a quad at (-1, -1, 1, 1); on another shape f != 1
// cut the movie (21:9: 12% lost above and below). 16 / 9 become the screen space's size (f = 1)
// and the quad takes the movie's shape inside the screen (bars, never stretched); the rasterizer
// clears the target first (MemoryPatcher::MovieFitRect).
static void ApplyMovieFit(u32 sw, u32 sh) {
    const double f = double(sh) * 16.0 / (double(sw) * 9.0);
    if (std::abs(f - 1.0) < 0.01) {
        return;
    }
    static constexpr float k_orig[2] = {16.0f, 9.0f};
    static constexpr float rect_orig[4] = {-1.0f, -1.0f, 1.0f, 1.0f};
    auto* k = reinterpret_cast<float*>(g_eboot_address + 0xaadbe0);
    auto* rect = reinterpret_cast<float*>(g_eboot_address + 0xaadd60);
    if (std::memcmp(k, k_orig, sizeof(k_orig)) != 0 || std::memcmp(rect, rect_orig, sizeof(rect_orig)) != 0) {
        std::printf("Screen shape: movies keep the 16:9 draw (constants differ in this eboot)\n");
        return;
    }
    const float x = f < 1.0 ? float(f) : 1.0f;
    const float y = f < 1.0 ? 1.0f : float(1.0 / f);
    const float k_new[2] = {float(sw), float(sh)};
    const float rect_new[4] = {-x, -y, x, y};
    std::memcpy(k, k_new, sizeof(k_new));
    std::memcpy(rect, rect_new, sizeof(rect_new));
    IssnShape::g_movie_x = x;
    IssnShape::g_movie_y = y;
}

// Render size and screen shape (axylpup 1.1 render height + SHADPS4_ASPECT, src/common/
// memory_patcher.cpp ApplyAspectRatio), decided at boot:
// - ISSN_RENDER_HEIGHT / ISSN_RENDER_SCALE (launcher: Internal resolution): the lines the game
//   renders (360 - 4320, 1080 = the game's own; above 2160 experimental).
// - ISSN_ASPECT (launcher: Screen shape): auto (default) = the game window's shape (fullscreen:
//   the monitor's), off = 16:9, or W:H (21:9, 32:9, 16:10, 4:3). Wider screens see more at the
//   sides (the vertical view stays), narrower ones keep the 16:9 width and see more above and
//   below (the projection's 2.0 becomes 2 / k); the HUD anchors to the new edges in a 2D screen
//   space kept at the 1080-line scale; movies keep their shape with bars.
// The renderer's size setup FUN_8001eff00 (render + output size) and the mode toggle FUN_8001f0be0
// load 1920 / 1080 immediates; the 2D screen space (HUD, camera aspect) is 0x1f0403 / 0x1f03fb.
static void ApplyRenderSize() {
    constexpr double base = 16.0 / 9.0;
    u32 h0 = 1080;
    if (const char* v = std::getenv("ISSN_RENDER_HEIGHT"); v && std::atoi(v) > 0) {
        h0 = static_cast<u32>(std::atoi(v));
    } else if (const char* p = std::getenv("ISSN_RENDER_SCALE"); p && std::atof(p) > 0.0) {
        h0 = static_cast<u32>(std::lround(1080.0 * std::atof(p) / 100.0));
    }
    const char* aspect_env = std::getenv("ISSN_ASPECT");
    const std::string mode = aspect_env && *aspect_env ? aspect_env : "auto";
    double aspect = base;
    int screen_w = 0, screen_h = 0;
    const bool have_screen = IssnShape::ScreenPixels(screen_w, screen_h) && screen_h > 0 && screen_w > 0;
    if (const auto colon = mode.find(':'); colon != std::string::npos) {
        const double w = std::atof(mode.substr(0, colon).c_str());
        const double h = std::atof(mode.substr(colon + 1).c_str());
        aspect = h > 0.0 && w > 0.0 ? w / h : base;
    } else if (mode == "auto" && have_screen) {
        aspect = double(screen_w) / double(screen_h);
    }
    // ISSN_UPSCALE_FACTOR (launcher: FSR 4 / DLSS quality): the window's 16:9-basis height divided
    // by it (Quality 1.5 ... Ultra Performance 3); the upscaler rebuilds the window size.
    double factor = 0.0;
    if (const char* f = std::getenv("ISSN_UPSCALE_FACTOR"); f && std::atof(f) >= 1.0) {
        factor = std::atof(f);
        const double basis = !have_screen ? 1080.0
                             : double(screen_w) / double(screen_h) >= base ? double(screen_h)
                                                                           : double(screen_w) / base;
        h0 = static_cast<u32>(std::lround(basis / factor));
    }
    h0 = std::clamp<u32>(Round8(h0), 360, 4320); // issn 10-28: 5K and 8K (testers' requests)
    // 3%: a "1280x720" window reports a client area like 1279x724 (axylpup's measurement).
    if (std::abs(aspect - base) < 0.03 * base) {
        aspect = base;
    }
    aspect = std::clamp(aspect, 1.0, 32.0 / 9.0);
    u32 width, height;
    SizeAt(h0, aspect, width, height);
    // At most 8K's pixels in any shape. axylpup capped this at 4K's (larger lost the device) before
    // the light list grew with the size (ApplyLightListCapacity, below); 5K / 8K are the
    // launcher's "experimental" sizes.
    while (u64(width) * height > 7680ull * 4320ull && h0 > 360) {
        h0 -= 8;
        SizeAt(h0, aspect, width, height);
    }
    if (width == 1920 && height == 1080) {
        return;
    }
    const bool narrow = aspect < base - 1e-6;
    // Narrow shapes: the projection builder 0x800581c90 computes P00 / P11 from one load of 2.0
    // (0x581d10); it is pointed at 2 / k in the 4 padding bytes before the function (0x581c84).
    static constexpr u8 two_load[8] = {0xc5, 0xfa, 0x10, 0x1d, 0x98, 0x27, 0x56, 0x00};
    static constexpr u8 pad[4] = {0x90, 0x90, 0x90, 0x90};
    if (narrow && (std::memcmp(reinterpret_cast<const void*>(g_eboot_address + 0x581d10), two_load, 8) != 0 ||
                   std::memcmp(reinterpret_cast<const void*>(g_eboot_address + 0x581c84), pad, 4) != 0)) {
        std::printf("Screen shape: %.3f refused (the projection code differs in this eboot); 16:9\n", aspect);
        aspect = base;
        SizeAt(h0, aspect, width, height);
        if (width == 1920 && height == 1080) {
            return;
        }
    }
    const bool shaped = aspect != base;
    // The HUD space stays at the 1080-line scale (axylpup s1440: at 1280x720 some HUD parts sized
    // in 1080p pixels broke); 16:9 keeps 1920x1080.
    const u32 sw = u32(std::lround(width * 1080.0 / h0)), sh = u32(std::lround(height * 1080.0 / h0));
    struct Imm {
        u64 offset;
        u32 original;
        bool width;
        bool screen_space;
    };
    static constexpr Imm imms[] = {
        {0x1eff4a, 0x780, true, false}, {0x1eff51, 0x438, false, false}, // render size
        {0x1eff57, 0x780, true, false}, {0x1eff5e, 0x438, false, false}, // output size
        {0x1f0403, 0x780, true, true},  {0x1f03fb, 0x438, false, true},  // 2D screen space
        {0x1f0d3e, 0x780, true, false}, {0x1f0d4d, 0x438, false, false}, // mode toggle
    };
    for (const auto& imm : imms) {
        u32 live = 0;
        std::memcpy(&live, reinterpret_cast<const void*>(g_eboot_address + imm.offset), 4);
        if (live != imm.original) {
            std::printf("Render size: unavailable (eboot+%#llx holds %#x, not %#x)\n",
                        static_cast<unsigned long long>(imm.offset), live, imm.original);
            return;
        }
    }
    for (const auto& imm : imms) {
        const u32 value = imm.screen_space ? (imm.width ? sw : sh) : (imm.width ? width : height);
        std::memcpy(reinterpret_cast<void*>(g_eboot_address + imm.offset), &value, 4);
    }
    ApplyLightListCapacity(width, height);
    if (narrow) {
        const float two_over_k = float(2.0 / (base / aspect));
        std::memcpy(reinterpret_cast<void*>(g_eboot_address + 0x581c84), &two_over_k, 4);
        const s32 disp = s32(0x581c84 - (0x581d10 + 8)); // rip-relative from the next instruction
        std::memcpy(reinterpret_cast<void*>(g_eboot_address + 0x581d10 + 4), &disp, 4);
    }
    IssnShape::g_aspect = aspect;
    // issn: the upscaler learns the game's size from its first resolve; told now, FSR 4 / DLSS
    // start at the right size and are not created twice. Made again after the first frame,
    // FSR 4 crashed inside the AMD driver (Lucas 10-27: 67% in a 1920x1078 window).
    Vulkan::Dlss::g_game_width.store(width, std::memory_order_relaxed);
    Vulkan::Dlss::g_game_height.store(height, std::memory_order_relaxed);
    if (shaped) {
        ApplyMovieFit(sw, sh);
    }
    if (factor > 0.0) {
        std::printf("Render size: the window %dx%d / %.1f (FSR 4 / DLSS quality)\n", screen_w, screen_h, factor);
    }
    std::printf("Render size: %ux%u (%.0f%% of 1080 lines)%s\n", width, height, h0 * 100.0 / 1080.0,
                shaped ? fmt::format(", screen shape {:.3f} ({}; window {}x{}), HUD space {}x{}{}", aspect,
                                     mode, screen_w, screen_h, sw, sh,
                                     narrow ? ", 16:9 width kept, more picture above and below" : "")
                             .c_str()
                       : "");
}
} // namespace

extern "C" void issngpu_apply_game_patches(void) {
    if (g_eboot_address == 0) {
        return;
    }
    ApplyParticleLateRead();
    ApplyParticleLightOneSubmit();
    ApplyHouseSplit();
    ApplyRenderSize();
}
