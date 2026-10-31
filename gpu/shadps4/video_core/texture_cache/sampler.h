// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "video_core/amdgpu/regs_texture.h"
#include "video_core/amdgpu/resource.h"
#include "video_core/renderer_vulkan/vk_common.h"
#include "video_core/renderer_vulkan/vk_native_bindings.h"

namespace Vulkan {
class Instance;
}

namespace VideoCore {

/// issn-axy: the anisotropy forced on the game's filtered colour samplers (ISSN_ANISO at start,
/// the in-game menu's Texture filtering live; 0 = the game's own). The texture cache keys its
/// samplers by it, so a change makes new samplers on the next draws.
float ForcedAniso();
void SetForcedAniso(float level);

class Sampler {
public:
    explicit Sampler(const Vulkan::Instance& instance, const AmdGpu::Sampler& sampler,
                     const AmdGpu::BorderColorBuffer border_color_base);
    ~Sampler();

    Sampler(const Sampler&) = delete;
    Sampler& operator=(const Sampler&) = delete;

    Sampler(Sampler&&) = default;
    Sampler& operator=(Sampler&&) = default;

    vk::Sampler Handle() const noexcept {
        return *handle;
    }

    size_t lru_id{};
    /// Descriptor heap slot, retained create info and border colour registration; inert in
    /// legacy mode.
    Vulkan::NativeSamplerState native;

private:
    vk::UniqueSampler handle;
};

} // namespace VideoCore
