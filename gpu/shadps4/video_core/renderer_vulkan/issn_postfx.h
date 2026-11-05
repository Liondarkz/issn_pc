// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <atomic>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <vector>
#include "common/types.h"

// issn 11-04 (Discord poll, 13 votes): switches for the game's motion blur and chromatic aberration.
// Each effect is one or more of the game's full-screen post passes, known by the pixel (or compute)
// shader's program hash, as fs_a6d23116 is the T2x resolve (vk_dlss.h). With a switch off, the
// pass is neutralised on the back (Rasterizer::PostFxDraw / PostFxDispatch): its target gets a
// copy of its input of the same size, or, with no such input, the draw is dropped.
//
// Finding the hashes: Insert > Image > "Find post effects (test)" lists the full-screen passes of
// the last frame up to the T2x resolve (after the scene, before the HUD) with their hash, target
// and inputs, and turns any of them off live; ISSN_POSTFX_LOG=1 writes the same list to the log
// once a second. The found hashes go in MotionBlurHashes / ChromaticHashes below, or, without a
// rebuild, in ISSN_POSTFX_MB / ISSN_POSTFX_CA (comma-separated hex).
namespace Vulkan::PostFx {

enum Effect : u32 { MotionBlur = 0, Chromatic = 1, EffectCount = 2 };

/// Built-in hashes per effect (CUSA00223 01.07). Empty until the in-game test names them.
inline constexpr u64 MotionBlurHashes[] = {0};
inline constexpr u64 ChromaticHashes[] = {0};

constexpr u32 MaxHashes = 8;
struct EffectPrograms {
    std::array<u64, MaxHashes> hashes{};
    u32 count = 0;
};

inline void ParseHashList(const char* text, EffectPrograms& out) {
    while (text && *text && out.count < MaxHashes) {
        char* end = nullptr;
        const u64 h = std::strtoull(text, &end, 16);
        if (end == text) {
            ++text;
            continue;
        }
        if (h != 0) {
            out.hashes[out.count++] = h;
        }
        text = end;
    }
}

/// The hashes of each effect: the built-in ones, then the environment's.
inline const std::array<EffectPrograms, EffectCount>& Programs() {
    static const std::array<EffectPrograms, EffectCount> programs = [] {
        std::array<EffectPrograms, EffectCount> p{};
        const auto add = [](EffectPrograms& e, const u64* list, size_t n, const char* env) {
            for (size_t i = 0; i < n && e.count < MaxHashes; ++i) {
                if (list[i] != 0) {
                    e.hashes[e.count++] = list[i];
                }
            }
            ParseHashList(std::getenv(env), e);
        };
        add(p[MotionBlur], MotionBlurHashes, std::size(MotionBlurHashes), "ISSN_POSTFX_MB");
        add(p[Chromatic], ChromaticHashes, std::size(ChromaticHashes), "ISSN_POSTFX_CA");
        return p;
    }();
    return programs;
}

/// Is the effect's pass known for this game build (the menu shows the switch only then)?
inline bool Known(Effect e) {
    return Programs()[e].count != 0;
}

/// The player's switches (Insert > Image, issn.ini motion_blur / chromatic_aberration): true =
/// the effect is off. Live.
inline std::array<std::atomic<bool>, EffectCount> g_off{};

/// The test list's live switches: up to 16 program hashes turned off by hand.
inline std::array<std::atomic<u64>, 16> g_test_off{};
/// Some hash in g_test_off (kept by SetTestOff, so a draw reads one flag, not 16).
inline std::atomic<bool> g_any_test{false};
/// Test: 0 = copy the input (as the effect switches do), 1 = drop the draw.
inline std::atomic<int> g_test_mode{0};

/// The menu's test section is open (the back collects the list only while it is, or with the log).
inline std::atomic<bool> g_collect{false};
inline bool LogEnabled() {
    static const bool on = [] {
        const char* v = std::getenv("ISSN_POSTFX_LOG");
        return v && v[0] == '1';
    }();
    return on;
}

/// Anything to do on the back: one relaxed load per switch, so the draw path stays as it was
/// while everything is on and the test is closed.
inline bool Active() {
    if (g_collect.load(std::memory_order_relaxed) || LogEnabled()) {
        return true;
    }
    for (const auto& off : g_off) {
        if (off.load(std::memory_order_relaxed)) {
            return true;
        }
    }
    return g_any_test.load(std::memory_order_relaxed);
}

enum class Action : u8 { Draw, Copy, Drop };

/// What to do with a pass of this program (back thread).
inline Action Decide(u64 hash) {
    if (hash == 0) {
        return Action::Draw;
    }
    for (u32 e = 0; e < EffectCount; ++e) {
        if (!g_off[e].load(std::memory_order_relaxed)) {
            continue;
        }
        const auto& p = Programs()[e];
        for (u32 i = 0; i < p.count; ++i) {
            if (p.hashes[i] == hash) {
                return Action::Copy;
            }
        }
    }
    for (const auto& h : g_test_off) {
        if (h.load(std::memory_order_relaxed) == hash) {
            return g_test_mode.load(std::memory_order_relaxed) == 1 ? Action::Drop : Action::Copy;
        }
    }
    return Action::Draw;
}

/// Turns one program off or on in the test list (menu).
inline void SetTestOff(u64 hash, bool off) {
    for (auto& h : g_test_off) {
        if (h.load(std::memory_order_relaxed) == hash) {
            if (!off) {
                h.store(0, std::memory_order_relaxed);
            }
            off = false; // already listed
        }
    }
    if (off) {
        for (auto& h : g_test_off) {
            u64 expected = 0;
            if (h.compare_exchange_strong(expected, hash, std::memory_order_relaxed)) {
                break;
            }
        }
    }
    bool any = false;
    for (const auto& h : g_test_off) {
        any |= h.load(std::memory_order_relaxed) != 0;
    }
    g_any_test.store(any, std::memory_order_relaxed);
}
inline bool IsTestOff(u64 hash) {
    for (const auto& h : g_test_off) {
        if (h.load(std::memory_order_relaxed) == hash) {
            return true;
        }
    }
    return false;
}

// ---- The list of full-screen passes ----

struct Input {
    VAddr addr{};
    u16 width{}, height{};
    u16 fmt{}; ///< data format | number format << 8
};
struct Pass {
    u64 hash{};      ///< pixel shader (or compute shader) program hash
    u64 vs_hash{};
    VAddr target{};  ///< colour target 0 (compute: 0)
    u16 width{}, height{};
    u16 fmt{};
    u8 compute{};
    u8 action{};     ///< Action taken this frame
    u8 inputs{};
    std::array<Input, 4> in{};
};

/// The frame's passes up to the T2x resolve; the back fills `building`, the resolve publishes it.
struct List {
    std::mutex mutex;
    std::vector<Pass> last; ///< the last published frame
    u64 frame = 0;          ///< published frames
};
inline List g_list;
constexpr size_t MaxPasses = 64; ///< the last ones before the resolve (older ones drop out)

} // namespace Vulkan::PostFx
