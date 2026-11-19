// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <atomic>
#include "common/types.h"

// issn 11-16: the port's HDR display mapping (post_process.frag, HDR frames only). The game's HDR
// output (vk_dlss.h "HDR output") reaches 14x its paper white with no regard for the display; this
// fits it to the display instead:
//  - paper white: the brightness of white paper / the HUD, in nits (0 = the game's own, read from
//    its final pass);
//  - peak: the display's peak brightness in nits; highlights above roll off smoothly to it
//    (ITU-R BT.2390 EETF on the max of R, G, B, so hues hold) instead of clipping;
//  - saturation: 1.0 = the game's.
// Defaults: ISSN_HDR_PAPER_WHITE / ISSN_HDR_PEAK / ISSN_HDR_SATURATION (issn.ini hdr_paper_white /
// hdr_peak / hdr_saturation through run.py), the peak otherwise from Windows (DXGI MaxLuminance of
// the HDR display). ISSN_HDR_MAPPING=0 leaves the game's HDR as it is. Live in Insert > Image.
namespace Vulkan::Hdr {

inline std::atomic<bool> g_mapping{true};
/// The swapchain is HDR10 now (the presenter sets it before the overlay draws).
inline std::atomic<bool> g_output_hdr{false};
inline std::atomic<float> g_peak_nits{1000.0f};
inline std::atomic<float> g_paper_white_nits{0.0f}; ///< 0 = the game's
inline std::atomic<float> g_saturation{1.0f};
/// The peak when the menu / issn.ini leave it on Auto (hdr_peak=0): ISSN_HDR_PEAK, else Windows,
/// else 1000; and where it came from ("Windows", "ISSN_HDR_PEAK" or "default").
inline std::atomic<float> g_auto_peak_nits{1000.0f};
inline std::atomic<const char*> g_peak_source{"default"};
/// The environment's paper white / saturation (used while issn.ini leaves them at their defaults).
inline std::atomic<float> g_env_paper_white_nits{0.0f};
inline std::atomic<float> g_env_saturation{1.0f};

/// Reads the environment and asks Windows for the HDR display's peak; once, at startup.
void Init();

/// The game's own paper white in nits (0 = not seen yet).
float GamePaperWhite();

} // namespace Vulkan::Hdr
