// SPDX-License-Identifier: GPL-2.0-or-later
// issn-axy: the in-game menu's live picture choices handed to the renderer (issn_settings.h
// ApplyLiveChoices). Apart from issn_settings.cpp, whose tests build without the video core.
#include "issn_settings.h"
#include "video_core/renderer_vulkan/issn_framegen.h"
#include "video_core/texture_cache/sampler.h"

#include <algorithm>

namespace IssnSettings {

void ApplyLiveChoices() {
    auto& v = Get();
    static constexpr float levels[] = {0.0f, 4.0f, 8.0f, 16.0f};
    VideoCore::SetForcedAniso(levels[std::clamp(v.menu_aniso.load(), 0, 3)]);
    if (Vulkan::FrameGen::Wanted()) {
        Vulkan::FrameGen::SetEnabled(v.menu_frame_gen.load() > 0);
    }
}

} // namespace IssnSettings
