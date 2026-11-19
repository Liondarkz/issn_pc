// SPDX-License-Identifier: GPL-2.0-or-later
// issn-axy: the in-game menu's live picture choices handed to the renderer (issn_settings.h
// ApplyLiveChoices). Apart from issn_settings.cpp, whose tests build without the video core.
#include "issn_settings.h"
#include "video_core/renderer_vulkan/issn_framegen.h"
#include "video_core/renderer_vulkan/issn_hdr.h"
#include "video_core/renderer_vulkan/issn_postfx.h"
#include "video_core/renderer_vulkan/issn_sharpen.h"
#include "video_core/texture_cache/sampler.h"

#include <algorithm>
#include <cstdlib>

namespace IssnSettings {

void ApplyLiveChoices() {
    auto& v = Get();
    static constexpr float levels[] = {0.0f, 4.0f, 8.0f, 16.0f};
    VideoCore::SetForcedAniso(levels[std::clamp(v.menu_aniso.load(), 0, 3)]);
    if (Vulkan::FrameGen::Wanted()) {
        Vulkan::FrameGen::SetEnabled(v.menu_frame_gen.load() > 0);
    }
    // issn 11-04: the game's motion blur and chromatic aberration (issn_postfx.h).
    Vulkan::Sharpen::g_strength.store(v.sharpening.load(), std::memory_order_relaxed); // issn 11-05
    Vulkan::PostFx::g_off[Vulkan::PostFx::MotionBlur].store(!v.motion_blur.load(), std::memory_order_relaxed);
    Vulkan::PostFx::g_off[Vulkan::PostFx::Chromatic].store(!v.chromatic_aberration.load(),
                                                           std::memory_order_relaxed);
    // issn 11-16: the HDR display mapping (issn_hdr.h). 0 / 100% = Auto / the environment's.
    Vulkan::Hdr::Init(); // once: the environment and the display's peak (Auto)
    namespace H = Vulkan::Hdr;
    const char* env_map = std::getenv("ISSN_HDR_MAPPING");
    H::g_mapping.store(v.hdr_mapping.load() && !(env_map && env_map[0] == '0'),
                       std::memory_order_relaxed);
    H::g_peak_nits.store(v.hdr_peak > 0 ? float(v.hdr_peak.load()) : H::g_auto_peak_nits.load(),
                         std::memory_order_relaxed);
    H::g_paper_white_nits.store(v.hdr_paper_white > 0 ? float(v.hdr_paper_white.load())
                                                      : H::g_env_paper_white_nits.load(),
                                std::memory_order_relaxed);
    H::g_saturation.store(v.hdr_saturation != 100 ? v.hdr_saturation.load() / 100.0f
                                                  : H::g_env_saturation.load(),
                          std::memory_order_relaxed);
}

} // namespace IssnSettings
