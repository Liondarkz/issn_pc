//  SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
//  SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "common/types.h"
#include "video_core/renderer_vulkan/vk_common.h"

namespace Vulkan {
class Frame;
}

namespace Vulkan::HostPasses {

class PostProcessingPass {
public:
    struct Settings {
        float gamma = 1.0f;
        u32 hdr = 0;
        // issn 11-16: HDR display mapping (issn_hdr.h); set per frame by the presenter.
        u32 hdr_map = 0;               ///< 1 = map (HDR frames only)
        float hdr_peak_nits = 1000.0f; ///< the display's peak
        float hdr_paper_scale = 1.0f;  ///< the player's paper white / the game's
        float hdr_src_peak_nits = 2800.0f; ///< the game's peak after that scale (14x paper white)
        float hdr_saturation = 1.0f;
    };

    void Create(vk::Device device, vk::Format surface_format);

    void Render(vk::CommandBuffer cmdbuf, vk::ImageView input, vk::Extent2D input_size,
                Frame& output, Settings settings);

private:
    vk::UniquePipeline pipeline{};
    vk::UniquePipelineLayout pipeline_layout{};
    vk::UniqueDescriptorSetLayout desc_set_layout{};
    vk::UniqueSampler sampler{};
};

} // namespace Vulkan::HostPasses
