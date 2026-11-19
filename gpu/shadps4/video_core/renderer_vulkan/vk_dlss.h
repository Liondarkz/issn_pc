// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>
#include "common/logging/log.h"
#include "common/types.h"
#include "shader_recompiler/info.h"
#include "video_core/amdgpu/regs.h"
#include "video_core/renderer_vulkan/vk_common.h"

// SHADPS4_DLSS (2026-10-17, LATER-PLAN 2; plan: notes/study-dlss-plan-2026-10-14.md, build D1;
// Streamline facts: notes/dlss-streamline-checklist-2026-10-17.md).
// Second Son's SMAA T2x temporal resolve (fs_a6d23116) reads exactly DLSS's inputs and writes the
// anti-aliased frame the HUD is drawn onto; that one draw per frame becomes a back-side action
// (Rasterizer::DrawClass::DlssResolve), so the worker paths carry it like a resolve or a depth copy
// and nothing is added per draw beyond one address compare in ClassifyDraw.
//   =1: DLAA through NVIDIA Streamline. The player's own signed DLLs are loaded from dlss\ next to
//       the EXE (sl.interposer, sl.common, sl.dlss, nvngx_dlss); only MIT headers are compiled in.
//       Missing / unsigned DLLs, a GPU without DLSS or any failure: the draw stays the game's own.
//   =2: diagnostic - the action copies the un-anti-aliased tonemap output (the game without T2x).
// Live keys: Home = DLSS on / off (instant A/B with the game's AA), Page Up = jitter sign combination.
namespace Vulkan::Dlss {

constexpr u64 ResolvePsHash = 0xa6d23116; ///< T2x temporal resolve (writes 0x26bd70000)
// HDR output (11-16, HdrProbe captures): with the game's HDR on, its post chain is another set of
// programs. The tonemap fs_2e624729 writes a 10:10:10:2 UNORM frame (a perceptual encoding: 0.37
// = paper white, 1.0 = 14x paper white, colour grading applied), its SMAA edges fs_2cdf42ec and
// T2x resolve fs_ef076052 read it, and the final pass fs_0d656790 decodes it, converts BT.709 to
// BT.2020, scales by its CB dword 9 (paper white / 100 nits) and encodes PQ. DLSS takes the same
// slot there: the 10-bit frame is its colour input, the resolve's target the HUD layer.
constexpr u64 ResolveHdrPsHash = 0xef076052; ///< T2x resolve, HDR output
constexpr u64 EdgesHdrPsHash = 0x2cdf42ec;   ///< SMAA edges, HDR output (reads the 10-bit frame)
constexpr u64 FinalHdrPsHash = 0x0d656790;   ///< the HDR final pass (PQ encode)
// Measured in every capture since 09-24 (study sec. 1.1-1.2): fixed guest addresses.
constexpr VAddr ColorInAddr = 0x2f9e10000; ///< tonemap output, RGBA8 1920x1080, not anti-aliased
constexpr VAddr MotionAddr = 0x3059b0000;  ///< RG16F motion vectors (UV, current minus previous)
constexpr VAddr DepthAddr = 0x26b200000;   ///< D32S8, reversed Z

inline int Mode() {
    static const int mode = [] {
        const char* v = std::getenv("SHADPS4_DLSS");
        if (v && (v[0] == '1' || v[0] == '2')) {
            return v[0] - '0';
        }
        // issn 11-04 (Discord poll): ISSN_AA_OFF=1 - anti-aliasing "Off", the player's name for
        // mode 2: the T2x resolve becomes a plain copy of the tonemap output and the camera
        // jitter is held at zero (memory_patcher.cpp JitterFrame), so the picture stays still.
        const char* off = std::getenv("ISSN_AA_OFF");
        if (off && off[0] == '1') {
            return 2;
        }
        // issn-axy: ISSN_FSR4=1 - FSR 4 Native AA in the same slot (issn_fsr4.h).
        const char* fsr4 = std::getenv("ISSN_FSR4");
        if (fsr4 && fsr4[0] == '1') {
            return 3;
        }
        // issn-axy: ISSN_XESS=1 - Intel XeSS in the same slot (issn_xess.h).
        const char* xess = std::getenv("ISSN_XESS");
        return xess && xess[0] == '1' ? 4 : 0;
    }();
    return mode;
}
inline bool Enabled() {
    return Mode() != 0;
}
/// FSR 4 or XeSS: the port's own upscalers, which share one path in the rasterizer.
inline bool OwnUpscaler() {
    return Mode() == 3 || Mode() == 4;
}
/// Anti-aliasing "Off" (ISSN_AA_OFF=1 or SHADPS4_DLSS=2): no T2x, no upscaler, no jitter.
inline bool AaOff() {
    return Mode() == 2;
}
/// The name players see for the running mode.
inline const char* ModeName() {
    switch (Mode()) {
    case 3:
        return "FSR 4";
    case 4:
        return "XeSS";
    case 2:
        return "Off";
    default:
        return "DLSS";
    }
}

/// Streamline is up and the feature exists on this device (=1), or the diagnostic copy (=2).
inline std::atomic<bool> g_ready{false};
/// Home key: the player's on / off (the game's own T2x runs while off).
inline std::atomic<bool> g_user_on{true};
/// Page Up: jitter sign combination (bit 0 flips x, bit 1 flips y).
inline std::atomic<u32> g_jitter_combo{3}; ///< D (-x -y): the user's live pick, 2026-10-17
/// Insert (SHADPS4_DLSS_UPSCALE): 1 = DLSS writes the window size, 1.5 = 1.5x the window (3840x2160
/// on a 1440p screen), scaled down by the post pass (supersampling; user idea 2026-10-17).
inline std::atomic<float> g_output_scale{1.0f};
/// The panel's last output pick above the screen size (Insert switches between it and 1.0).
inline std::atomic<float> g_output_pick{1.5f};
/// The network (sl::DLSSPreset number; 0 = the DLL's default, 11 = K, 12 = L, 13 = M): the Page Up
/// panel, first from SHADPS4_DLSS_PRESET.
inline std::atomic<u32> g_preset{[] {
    const char* v = std::getenv("SHADPS4_DLSS_PRESET");
    return v ? static_cast<u32>(std::atoi(v)) : 0u;
}()};
/// What the last options call set (the panel shows it): render -> output size and the mode
/// (sl::DLSSMode: 0 off, 1 Performance, 2 Balanced, 3 Quality, 4 Ultra Performance, 5 Ultra
/// Quality, 6 DLAA).
inline std::atomic<u32> g_status_in_w{0}, g_status_in_h{0}, g_status_out_w{0}, g_status_out_h{0};
inline std::atomic<u32> g_status_mode{0};
inline std::atomic<u64> g_status_evaluates{0};

/// Guest code address of fs_a6d23116 once a pipeline lookup has met it (0 = not yet / off).
inline std::atomic<u64> g_resolve_ps{0};
/// The same for the HDR output's resolve fs_ef076052.
inline std::atomic<u64> g_resolve_hdr_ps{0};
/// A frame's SMAA edges draw has told where the colour input is ("Any render size" below): until
/// then the game's own resolve runs (the first frames of a session).
inline std::atomic<bool> g_inputs_seen{false};

/// ISSN_HDR_DLSS=0: the port's AA steps aside while the game outputs HDR (its own T2x runs), as
/// before 11-16. Only DLSS (modes 0 / 1) takes the HDR resolve: FSR 4, XeSS and AA "Off" also set
/// g_ready, but their paths are untested on the 10-bit frame, so they step aside in HDR as before.
inline bool HdrAaWanted() {
    static const bool wanted = [] {
        const char* v = std::getenv("ISSN_HDR_DLSS");
        return Mode() <= 1 && !(v && v[0] == '0');
    }();
    return wanted;
}

/// PipelineCache::GetProgram: remembers where the resolve shader lives. Also with the game's own
/// AA (issn 11-05: the sharpening follows its resolve, vk_rasterizer.cpp SharpenGameResolve);
/// IsResolve still needs g_ready, which only the port's AA modes set.
inline void NoteProgram(bool fragment, u64 hash, VAddr base) {
    if (fragment && hash == ResolvePsHash) [[unlikely]] {
        g_resolve_ps.store(base, std::memory_order_relaxed);
    } else if (fragment && hash == ResolveHdrPsHash && HdrAaWanted()) [[unlikely]] {
        g_resolve_hdr_ps.store(base, std::memory_order_relaxed);
    }
}

/// ClassifyDraw: is this draw's pixel shader the resolve, and is DLSS to replace it now?
inline bool IsResolve(VAddr ps_address) {
    const u64 a = g_resolve_ps.load(std::memory_order_relaxed);
    const u64 h = g_resolve_hdr_ps.load(std::memory_order_relaxed);
    return ps_address != 0 && (a == ps_address || h == ps_address) &&
           g_ready.load(std::memory_order_relaxed) &&
           g_user_on.load(std::memory_order_relaxed) &&
           g_inputs_seen.load(std::memory_order_relaxed);
}

// The frame's jitter phase (study sec. 1.4): the game alternates two quarter-pixel offsets and
// writes the matching T2x subsample index (1 or 2; 0 = T2x off) into the SMAA blend-weights
// draw's 80-byte CB, float 12. That draw runs two draws before the resolve in the same command
// buffer, so the thread parsing it keeps the value and the resolve's record carries it to the back
// (Rasterizer::SplitDraw::dlss_phase): exact per frame, no guess from a frame counter that a
// loading screen could flip.
constexpr u64 WeightsPsHash = 0x961c93ba; ///< SMAA blend weights
inline std::atomic<u64> g_weights_ps{0};
inline std::atomic<u32> g_weights_ud{~0u}; ///< dword of its CB's V# in the flattened user data
/// When the V# is not in the 16 user-data registers: the shader's SRT walker (it reads the guest
/// pointer tables into the flattened buffer, as binding does) and that buffer's size.
inline std::atomic<Shader::PFN_SrtWalker> g_weights_walker{nullptr};
inline std::atomic<u32> g_weights_flat_dw{0};
/// The parsing thread's last weights draw: 1 / 2 = subsample index, 3 = T2x off, 0 = unknown,
/// 4 = an explicit offset (the emulator's Halton jitter, memory_patcher.cpp: t_jitter).
inline thread_local u8 t_phase = 0;
inline thread_local float t_jitter[2] = {}; ///< phase 4: the frame's (jx, jy) in render px

/// Where the weights program's Info is known: remember its address and its CB's user-data slot.
inline void NoteWeightsInfo(const Shader::Info& info, VAddr base) {
    if (info.pgm_hash != WeightsPsHash || info.stage != Shader::Stage::Fragment || !Enabled())
        [[likely]] {
        return;
    }
    if (g_weights_ps.load(std::memory_order_relaxed) == base) {
        return;
    }
    for (const auto& b : info.buffers) {
        if (b.IsSpecial() || b.inline_cbuf) {
            continue;
        }
        if (b.sharp_idx + 4 <= 16) {
            g_weights_walker.store(nullptr, std::memory_order_relaxed);
        } else if (info.srt_info.walker_func != nullptr &&
                   b.sharp_idx + 4 <= info.srt_info.flattened_bufsize_dw &&
                   info.srt_info.flattened_bufsize_dw <= 1024) {
            // Play log 2026-10-17: the phase stayed "unknown" on every frame - the V# comes
            // through a pointer table, not the 16 registers.
            g_weights_flat_dw.store(info.srt_info.flattened_bufsize_dw, std::memory_order_relaxed);
            g_weights_walker.store(info.srt_info.walker_func, std::memory_order_relaxed);
        } else {
            continue;
        }
        g_weights_ud.store(b.sharp_idx, std::memory_order_relaxed);
        g_weights_ps.store(base, std::memory_order_release);
        LOG_INFO(Render_Vulkan, "DLSS: jitter source fs_961c93ba at {:#x}, CB V# at dword {} ({})",
                 base, b.sharp_idx,
                 b.sharp_idx + 4 <= 16 ? "user data" : "pointer table");
        return;
    }
    static std::atomic<bool> warned{false};
    if (!warned.exchange(true)) {
        LOG_WARNING(Render_Vulkan, "DLSS: fs_961c93ba has no readable CB ({} buffers) - jitter "
                                   "phase unknown",
                    info.buffers.size());
    }
}

/// Next to every ClassifyDraw call that parses the stream in order (front, lean workers, inline).
// ---- Any render size (2026-10-20, v1.1 part C) ----
// The addresses above hold at the game's 1920x1080. When the game renders at another size
// (SHADPS4_ASPECT, SHADPS4_RENDER_HEIGHT, a community resolution patch) its targets sit elsewhere
// (a 1440p patch: no input at 0x2f9e10000, a yellow picture - RTX 5070 Ti report, 10-20). So the
// inputs are read every frame from the draws that use them, on the parsing thread (NoteDraw):
//  - SMAA edges fs_abc3c768 samples the tonemap output (its RGBA8 texture) with the scene depth
//    attached (rooftop replay EID 39235, measured);
//  - the resolve fs_a6d23116 samples the motion vectors (its one RG16F texture; its size = the
//    game's render size) and writes the HUD layer (its colour target).
// Until a frame has run them, the 1080p addresses stand (the values learned at 1080p equal them).
constexpr u64 EdgesPsHash = 0xabc3c768;
struct ShaderTap {
    std::atomic<u64> ps{0};
    std::atomic<Shader::PFN_SrtWalker> walker{nullptr};
    std::atomic<u32> flat_dw{0};
    std::atomic<u32> count{0};
    std::array<std::atomic<u32>, 8> sharps{}; ///< sharp_idx | 0x80000000 for a 128-bit T#
};
inline ShaderTap g_edges_tap;
inline ShaderTap g_resolve_tap;
inline ShaderTap g_edges_hdr_tap;   ///< fs_2cdf42ec (HDR output)
inline ShaderTap g_resolve_hdr_tap; ///< fs_ef076052 (HDR output)

// The HDR final pass's paper-white scale (its CB dword 9: nits / 100), read from its draws as the
// weights CB is (NoteDraw); 0 = not seen yet. The DLSS composite encodes PQ with it as the game
// does, and the display mapping (pp_pass) uses it as the game's paper white.
inline std::atomic<u64> g_final_hdr_ps{0};
inline std::atomic<u32> g_final_hdr_ud{~0u};
inline std::atomic<Shader::PFN_SrtWalker> g_final_hdr_walker{nullptr};
inline std::atomic<u32> g_final_hdr_flat_dw{0};
inline std::atomic<float> g_hdr_scale{0.0f};
inline void NoteFinalHdrInfo(const Shader::Info& info, VAddr base) {
    if (info.pgm_hash != FinalHdrPsHash || info.stage != Shader::Stage::Fragment) [[likely]] {
        return;
    }
    if (g_final_hdr_ps.load(std::memory_order_relaxed) == base) {
        return;
    }
    for (const auto& b : info.buffers) {
        if (b.IsSpecial() || b.inline_cbuf) {
            continue;
        }
        if (b.sharp_idx + 4 <= 16) {
            g_final_hdr_walker.store(nullptr, std::memory_order_relaxed);
        } else if (info.srt_info.walker_func != nullptr &&
                   b.sharp_idx + 4 <= info.srt_info.flattened_bufsize_dw &&
                   info.srt_info.flattened_bufsize_dw <= 1024) {
            g_final_hdr_flat_dw.store(info.srt_info.flattened_bufsize_dw, std::memory_order_relaxed);
            g_final_hdr_walker.store(info.srt_info.walker_func, std::memory_order_relaxed);
        } else {
            continue;
        }
        g_final_hdr_ud.store(b.sharp_idx, std::memory_order_relaxed);
        g_final_hdr_ps.store(base, std::memory_order_release);
        LOG_INFO(Render_Vulkan, "HDR: final pass fs_0d656790 at {:#x}, CB V# at dword {}", base,
                 b.sharp_idx);
        return;
    }
}
inline void NoteFinalHdrDraw(const AmdGpu::Regs& regs) {
    const u32 ud = g_final_hdr_ud.load(std::memory_order_relaxed);
    AmdGpu::Buffer vsharp;
    if (ud + 4 <= 16) {
        std::memcpy(&vsharp, &regs.ps_program.user_data[ud], sizeof(vsharp));
    } else {
        const auto walker = g_final_hdr_walker.load(std::memory_order_relaxed);
        const u32 flat_dw = g_final_hdr_flat_dw.load(std::memory_order_relaxed);
        if (walker == nullptr || ud + 4 > flat_dw) {
            return;
        }
        thread_local std::vector<u32> flat;
        flat.assign(flat_dw, 0u);
        std::memcpy(flat.data(), regs.ps_program.user_data.data(), 16 * sizeof(u32));
        walker(regs.ps_program.user_data.data(), flat.data());
        std::memcpy(&vsharp, &flat[ud], sizeof(vsharp));
    }
    if (vsharp.base_address == 0 || vsharp.GetSize() < 40) {
        return;
    }
    float scale;
    std::memcpy(&scale, reinterpret_cast<const void*>(vsharp.base_address + 36), sizeof(scale));
    if (scale > 0.01f && scale < 100.0f) {
        const float old = g_hdr_scale.exchange(scale, std::memory_order_relaxed);
        if (old != scale) {
            LOG_WARNING(Render_Vulkan, "HDR: the game's paper white is {:.0f} nits", scale * 100.0f);
        }
    }
}
inline std::atomic<VAddr> g_color_addr{ColorInAddr};
inline std::atomic<VAddr> g_motion_addr{MotionAddr};
inline std::atomic<VAddr> g_depth_addr{DepthAddr};
inline std::atomic<VAddr> g_hud_addr{0x26bd70000}; ///< the resolve's target = the HUD layer
inline std::atomic<u32> g_game_width{1920};
inline std::atomic<u32> g_game_height{1080};

/// Where a tapped program's Info is known: its texture slots in the (flattened) user data.
inline void NoteTapInfo(ShaderTap& tap, const Shader::Info& info, VAddr base) {
    if (tap.ps.load(std::memory_order_relaxed) == base) [[likely]] {
        return;
    }
    const bool flat = info.srt_info.walker_func != nullptr &&
                      info.srt_info.flattened_bufsize_dw <= 1024;
    const u32 limit = flat ? info.srt_info.flattened_bufsize_dw : 16u;
    u32 n = 0;
    for (const auto& img : info.images) {
        const u32 size_dw = img.is_r128 ? 4u : 8u;
        if (img.sharp_idx + size_dw > limit || n >= tap.sharps.size()) {
            continue;
        }
        tap.sharps[n++].store(img.sharp_idx | (img.is_r128 ? 0x80000000u : 0u),
                              std::memory_order_relaxed);
    }
    tap.count.store(n, std::memory_order_relaxed);
    tap.walker.store(flat ? info.srt_info.walker_func : nullptr, std::memory_order_relaxed);
    tap.flat_dw.store(flat ? info.srt_info.flattened_bufsize_dw : 16u, std::memory_order_relaxed);
    tap.ps.store(base, std::memory_order_release);
    LOG_INFO(Render_Vulkan, "DLSS: input tap fs_{:08x} at {:#x}, {} of {} textures readable ({})",
             info.pgm_hash, base, n, info.images.size(), flat ? "pointer table" : "user data");
}

/// The pipeline lookup's Info sites: the weights CB and the two input taps.
/// SHADPS4_SKIP_PS=<hex hash> (test, 10-21): draws with this pixel shader are skipped, e.g.
/// 204f45fe = the screen-space reflection ray march (study-delsin-mv-ssr-2026-10-21).
inline std::atomic<u64> g_skip_ps{0};
inline u64 SkipPsHash() {
    static const u64 hash = [] {
        const char* v = std::getenv("SHADPS4_SKIP_PS");
        return v ? std::strtoull(v, nullptr, 16) : 0ull;
    }();
    return hash;
}

// ---- HDR probe (ISSN_HDR_PROBE=1, diagnostic) ----
// Logs each distinct full-screen pass (pixel shader hash, colour target, its textures) once, on
// every draw path (NoteDraw runs on the front, the lean workers and inline), to find the game's
// tonemap pass (the one writing the DLSS colour input) and the HDR scene it reads.
inline bool HdrProbeOn() {
    static const bool on = [] {
        const char* v = std::getenv("ISSN_HDR_PROBE");
        return v && v[0] == '1';
    }();
    return on;
}
struct ProbeEntry {
    std::atomic<VAddr> base{0};
    std::atomic<u64> hash{0};
    ShaderTap tap;
};
inline std::array<ProbeEntry, 4096> g_probe_table{};
inline ProbeEntry* ProbeFind(VAddr base, bool insert) {
    if (base == 0) {
        return nullptr;
    }
    const size_t mask = g_probe_table.size() - 1;
    for (size_t i = (base >> 8) * 0x9E3779B97F4A7C15ull & mask, n = 0; n < 64; ++n, i = (i + 1) & mask) {
        VAddr cur = g_probe_table[i].base.load(std::memory_order_acquire);
        if (cur == base) {
            return &g_probe_table[i];
        }
        if (cur == 0) {
            if (!insert) {
                return nullptr;
            }
            VAddr expected = 0;
            if (g_probe_table[i].base.compare_exchange_strong(expected, base) || expected == base) {
                return &g_probe_table[i];
            }
        }
    }
    return nullptr;
}
inline void NoteTapInfoQuiet(ShaderTap& tap, const Shader::Info& info, VAddr base);
inline void ProbeRegister(const Shader::Info& info, VAddr base) {
    if (info.stage != Shader::Stage::Fragment) {
        return;
    }
    if (ProbeEntry* e = ProbeFind(base, true); e && e->hash.load(std::memory_order_relaxed) == 0) {
        NoteTapInfoQuiet(e->tap, info, base);
        e->hash.store(info.pgm_hash, std::memory_order_release);
    }
}

inline void NoteShaderInfo(const Shader::Info& info, VAddr base) {
    if (HdrProbeOn()) [[unlikely]] {
        ProbeRegister(info, base);
    }
    if (info.stage == Shader::Stage::Fragment && SkipPsHash() != 0 &&
        info.pgm_hash == SkipPsHash()) [[unlikely]] {
        g_skip_ps.store(base, std::memory_order_relaxed);
    }
    if (info.stage == Shader::Stage::Fragment) {
        NoteFinalHdrInfo(info, base);
    }
    if (info.stage != Shader::Stage::Fragment || !Enabled()) [[likely]] {
        return;
    }
    if (info.pgm_hash == EdgesPsHash) {
        NoteTapInfo(g_edges_tap, info, base);
    } else if (info.pgm_hash == ResolvePsHash) {
        NoteTapInfo(g_resolve_tap, info, base);
    } else if (info.pgm_hash == EdgesHdrPsHash && HdrAaWanted()) {
        NoteTapInfo(g_edges_hdr_tap, info, base);
    } else if (info.pgm_hash == ResolveHdrPsHash && HdrAaWanted()) {
        NoteTapInfo(g_resolve_hdr_tap, info, base);
    }
}

/// A tapped draw's textures (T#), read from its user data as binding does.
template <typename Fn>
inline void ForEachTapImage(const ShaderTap& tap, const AmdGpu::Regs& regs, Fn&& fn) {
    const u32 n = tap.count.load(std::memory_order_relaxed);
    const auto walker = tap.walker.load(std::memory_order_relaxed);
    const u32* ud = regs.ps_program.user_data.data();
    thread_local std::vector<u32> flat;
    if (walker != nullptr) {
        flat.assign(std::max(tap.flat_dw.load(std::memory_order_relaxed), 16u), 0u);
        std::memcpy(flat.data(), ud, 16 * sizeof(u32));
        walker(ud, flat.data());
        ud = flat.data();
    }
    for (u32 i = 0; i < n; ++i) {
        const u32 s = tap.sharps[i].load(std::memory_order_relaxed);
        AmdGpu::Image image{};
        std::memcpy(&image, ud + (s & 0x7fffffffu), (s & 0x80000000u) ? 16 : sizeof(image));
        if (s & 0x80000000u) {
            image.pitch = image.width;
        }
        if (image.Valid() && image.Address() != 0) {
            fn(image);
        }
    }
}

template <typename T>
inline void StoreIfChanged(std::atomic<T>& a, T v) {
    if (a.load(std::memory_order_relaxed) != v) {
        a.store(v, std::memory_order_relaxed);
    }
}

/// NoteDraw: the edges draw -> colour input + depth; the resolve -> motion vectors, render size,
/// HUD layer.
inline void NoteInputDraw(const AmdGpu::Regs& regs, VAddr ps) {
    // The HDR output's programs read a 10:10:10:2 frame where the SDR ones read RGBA8.
    const bool hdr_edges = ps == g_edges_hdr_tap.ps.load(std::memory_order_acquire);
    const bool hdr_resolve = !hdr_edges && ps == g_resolve_hdr_tap.ps.load(std::memory_order_acquire);
    if (hdr_edges || ps == g_edges_tap.ps.load(std::memory_order_acquire)) {
        const auto colour_fmt = hdr_edges ? AmdGpu::DataFormat::Format2_10_10_10
                                          : AmdGpu::DataFormat::Format8_8_8_8;
        bool found = false;
        ForEachTapImage(hdr_edges ? g_edges_hdr_tap : g_edges_tap, regs,
                        [&](const AmdGpu::Image& image) {
            if (!found && image.GetDataFmt() == colour_fmt) {
                StoreIfChanged(g_color_addr, image.Address());
                StoreIfChanged(g_inputs_seen, true);
                found = true;
            }
        });
        if (const VAddr z = regs.depth_buffer.DepthAddress(); z != 0) {
            StoreIfChanged(g_depth_addr, z);
        }
    } else if (hdr_resolve || ps == g_resolve_tap.ps.load(std::memory_order_acquire)) {
        bool found = false;
        ForEachTapImage(hdr_resolve ? g_resolve_hdr_tap : g_resolve_tap, regs,
                        [&](const AmdGpu::Image& image) {
            if (!found && image.GetDataFmt() == AmdGpu::DataFormat::Format16_16 &&
                image.GetNumberFmt() == AmdGpu::NumberFormat::Float) {
                StoreIfChanged(g_motion_addr, image.Address());
                StoreIfChanged(g_game_width, static_cast<u32>(image.width) + 1);
                StoreIfChanged(g_game_height, static_cast<u32>(image.height) + 1);
                found = true;
            }
        });
        StoreIfChanged(g_hud_addr, static_cast<VAddr>(regs.color_buffers[0].Address()));
    }
}

inline void NoteTapInfoQuiet(ShaderTap& tap, const Shader::Info& info, VAddr base) {
    const bool flat = info.srt_info.walker_func != nullptr &&
                      info.srt_info.flattened_bufsize_dw <= 1024;
    const u32 limit = flat ? info.srt_info.flattened_bufsize_dw : 16u;
    u32 n = 0;
    for (const auto& img : info.images) {
        const u32 size_dw = img.is_r128 ? 4u : 8u;
        if (img.sharp_idx + size_dw > limit || n >= tap.sharps.size()) {
            continue;
        }
        tap.sharps[n++].store(img.sharp_idx | (img.is_r128 ? 0x80000000u : 0u),
                              std::memory_order_relaxed);
    }
    tap.count.store(n, std::memory_order_relaxed);
    tap.walker.store(flat ? info.srt_info.walker_func : nullptr, std::memory_order_relaxed);
    tap.flat_dw.store(flat ? info.srt_info.flattened_bufsize_dw : 16u, std::memory_order_relaxed);
    tap.ps.store(base, std::memory_order_release);
}

inline void ProbeDraw(const AmdGpu::Regs& regs, VAddr ps) {
    const auto& cb = regs.color_buffers[0];
    const VAddr target = cb.Address();
    const u32 width = cb.Pitch();
    const u32 height = cb.Height();
    if (target == 0 || regs.num_indices > 6 || width * 4 < g_game_width.load(std::memory_order_relaxed)) {
        return;
    }
    const ProbeEntry* e = ProbeFind(ps, false);
    const u64 hash = e ? e->hash.load(std::memory_order_acquire) : 0;
    static std::mutex m;
    static std::vector<std::pair<u64, VAddr>> seen;
    static u32 order = 0;
    std::string inputs;
    {
        std::scoped_lock lk{m};
        for (const auto& s : seen) {
            if (s.first == (hash ? hash : ps) && s.second == target) {
                return;
            }
        }
        if (seen.size() >= 512) {
            return;
        }
        seen.emplace_back(hash ? hash : ps, target);
        ++order;
    }
    if (e && hash != 0) {
        ForEachTapImage(e->tap, regs, [&](const AmdGpu::Image& image) {
            inputs += fmt::format(" {:#x} {}x{} d{} n{}", image.Address(), u32(image.width) + 1,
                                  u32(image.height) + 1, u32(image.GetDataFmt()),
                                  u32(image.GetNumberFmt()));
        });
    }
    const VAddr color_in = g_color_addr.load(std::memory_order_relaxed);
    const char* tag = target == color_in                          ? " [TONEMAP OUT = DLSS colour in]"
                      : target == g_hud_addr.load(std::memory_order_relaxed) ? " [HUD layer]"
                      : hash == ResolvePsHash                     ? " [T2x resolve]"
                      : hash == EdgesPsHash                       ? " [SMAA edges]"
                                                                  : "";
    LOG_WARNING(Render_Vulkan, "HdrProbe #{}: fs_{:08x} (ps {:#x}) -> {:#x} {}x{} d{} n{}; in:{}{}",
                order, hash, ps, target, width, height, u32(cb.GetDataFmt()),
                u32(cb.GetNumberFmt()), inputs.empty() ? " none/unknown" : inputs, tag);
}

inline void NoteDraw(const AmdGpu::Regs& regs) {
    const VAddr ps = regs.ps_program.Address<VAddr>();
    if (HdrProbeOn()) [[unlikely]] {
        ProbeDraw(regs, ps);
    }
    const u64 w = g_weights_ps.load(std::memory_order_relaxed);
    if (w == 0 || ps != w) [[likely]] {
        if (ps == g_edges_tap.ps.load(std::memory_order_relaxed) ||
            ps == g_resolve_tap.ps.load(std::memory_order_relaxed) ||
            ps == g_edges_hdr_tap.ps.load(std::memory_order_relaxed) ||
            ps == g_resolve_hdr_tap.ps.load(std::memory_order_relaxed)) [[unlikely]] {
            if (ps != 0) {
                NoteInputDraw(regs, ps);
            }
        } else if (ps != 0 && ps == g_final_hdr_ps.load(std::memory_order_relaxed)) [[unlikely]] {
            NoteFinalHdrDraw(regs);
        }
        return;
    }
    const u32 ud = g_weights_ud.load(std::memory_order_relaxed);
    AmdGpu::Buffer vsharp;
    if (ud + 4 <= 16) {
        std::memcpy(&vsharp, &regs.ps_program.user_data[ud], sizeof(vsharp));
    } else {
        const auto walker = g_weights_walker.load(std::memory_order_relaxed);
        const u32 flat_dw = g_weights_flat_dw.load(std::memory_order_relaxed);
        if (walker == nullptr || ud + 4 > flat_dw) {
            t_phase = 0;
            return;
        }
        thread_local std::vector<u32> flat;
        flat.assign(flat_dw, 0u);
        std::memcpy(flat.data(), regs.ps_program.user_data.data(), 16 * sizeof(u32));
        walker(regs.ps_program.user_data.data(), flat.data());
        std::memcpy(&vsharp, &flat[ud], sizeof(vsharp));
    }
    if (vsharp.base_address == 0 || vsharp.GetSize() < 60) {
        t_phase = 0;
        return;
    }
    float f;
    std::memcpy(&f, reinterpret_cast<const void*>(vsharp.base_address + 48), sizeof(f));
    float tag;
    std::memcpy(&tag, reinterpret_cast<const void*>(vsharp.base_address + 56), sizeof(tag));
    if (tag >= 99.5f) {
        t_phase = 4;
        t_jitter[0] = f;
        std::memcpy(&t_jitter[1], reinterpret_cast<const void*>(vsharp.base_address + 52),
                    sizeof(float));
        return;
    }
    t_phase = (f > 0.5f && f < 1.5f)   ? 1
              : (f > 1.5f && f < 2.5f) ? 2
              : (f > -0.5f && f < 0.5f) ? 3
                                        : 0;
}

// ---- Streamline (vk_dlss.cpp; Windows only, stubs elsewhere) ----

/// vk_platform GetInstanceExtensions: loads + verifies the interposer, slInit, and appends the
/// instance extensions DLSS needs. Before vkCreateInstance (Streamline's required order).
void AddInstanceExtensions(std::vector<const char*>& extensions);
/// vk_instance, device selection: slIsFeatureSupported for this GPU; false = DLSS stays off.
bool DeviceSupported(vk::PhysicalDevice physical_device);
/// The device extensions DLSS asked for (valid until Shutdown).
const std::vector<const char*>& DeviceExtensions();
/// vk_instance, after the device and its queues exist: slSetVulkanInfo, present proxy, DLSS
/// functions; sets g_ready on success.
void DeviceCreated(vk::Instance instance, vk::PhysicalDevice physical_device, vk::Device device,
                   u32 graphics_family, u32 graphics_index);
/// vk_swapchain: the present to use (Streamline's proxy runs its per-frame bookkeeping and
/// forwards); null = present as usual.
PFN_vkQueuePresentKHR PresentFunction();
/// Instance destruction (GPU idle): slShutdown before the device goes.
void Shutdown();

struct EvalImage {
    vk::Image image;
    vk::ImageView view;
    vk::Format format;
    vk::ImageLayout layout;
    vk::ImageUsageFlags usage;
    u32 width;
    u32 height;
    bool depth;
};
/// One DLAA evaluate into `cmdbuf` (outside rendering; the images already in `layout`, with
/// their producer barriers). jitter in pixels. False = not run (the caller falls back).
bool Evaluate(vk::CommandBuffer cmdbuf, u64 frame, const EvalImage& color, const EvalImage& motion,
              const EvalImage& depth, const EvalImage& output, float jitter_x, float jitter_y,
              bool reset);

} // namespace Vulkan::Dlss
