// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <atomic>
#include <deque>
#include <utility>
#include <bit>
#include <cmath>
#include <memory>
#include "common/types.h"

// issn 11-05: sharpening, 0 to 100 % (issn.ini sharpening, Insert > Image, the launcher's
// Graphics), off by default and live. Applied once, by whoever owns the scene's last step before
// the HUD (the T2x resolve point, vk_rasterizer.cpp DlssResolve / SharpenGameResolve):
//   - FSR 4: its own RCAS (issn_fsr4.cpp), at strength / 100;
//   - XeSS with OptiScaler in bin\ (it takes the XeSS input): OptiScaler's sharpening, set by the
//     launcher in OptiScaler.ini ([Sharpness] Sharpness, [CAS] Enabled) at start;
//   - everything else (the game's own T2x, anti-aliasing Off, DLSS, XeSS): the port's pass, AMD's
//     RCAS (host_shaders/issn_sharpen.comp) on the resolve's output. DLSS (2.5.1 on) and XeSS have
//     no sharpening of their own any more, so it is still applied once.
// The presenter's FSR 1 / RCAS (ISSN_FSR1) runs only when FSR 1 scales up, and the menu has no FSR
// 1 since 2.2.0, so the two never meet.
namespace Vulkan::Sharpen {

/// The player's strength in percent (0 = off), live.
inline std::atomic<int> g_strength{0};

inline int ClampStrength(int percent) {
    return percent < 0 ? 0 : percent > 100 ? 100 : percent;
}
inline int Strength() {
    return ClampStrength(g_strength.load(std::memory_order_relaxed));
}

/// RCAS attenuation in stops for a strength: 100 % = 0 (the sharpest RCAS allows), 1 % ~ 2 stops.
inline float Attenuation(int percent) {
    return 2.0f * (1.0f - static_cast<float>(ClampStrength(percent)) / 100.0f);
}

/// FsrRcasCon (ffx_fsr1.h, MIT) for the 32-bit path: con.x = exp2(-attenuation) as bits.
inline std::array<u32, 4> RcasConstants(float attenuation) {
    const float sharpness = std::exp2(-attenuation);
    return {std::bit_cast<u32>(sharpness), 0u, 0u, 0u};
}

enum class Owner : u8 {
    None,       ///< strength 0
    Port,       ///< the port's RCAS pass on the resolve's output
    Fsr4,       ///< FSR 4's own RCAS
    OptiScaler, ///< OptiScaler (it upscales the XeSS input)
};

/// Who sharpens this frame. mode = Dlss::Mode() (0 the game's T2x, 1 DLSS, 2 Off, 3 FSR 4,
/// 4 XeSS); upscaler_ran = the upscaler wrote this frame (a frame it skipped is a plain copy).
inline Owner OwnerFor(int percent, int mode, bool optiscaler, bool upscaler_ran) {
    if (ClampStrength(percent) == 0) {
        return Owner::None;
    }
    if (mode == 3 && upscaler_ran) {
        return Owner::Fsr4;
    }
    if (mode == 4 && optiscaler && upscaler_ran) {
        return Owner::OptiScaler;
    }
    return Owner::Port;
}

/// OptiScaler beside the executable (bin\winmm.dll and bin\OptiScaler.ini, as the launcher puts
/// it). Windows only; read once.
bool OptiScalerPresent();

} // namespace Vulkan::Sharpen

#ifndef ISSN_SHARPEN_LOGIC_ONLY
#include "video_core/renderer_vulkan/vk_common.h"
#include "video_core/texture_cache/image.h"

namespace Vulkan {
class Instance;
}

namespace Vulkan::Sharpen {

/// The port's RCAS pass. Run records into `cmdbuf` (outside rendering): `image` (2D, level 0,
/// layer 0) is in `layout` with its writes done before; it ends in `layout` again, the result
/// visible to all later commands. Through two images of its own (a copy in the image's format,
/// the RCAS result in RGBA16F) and a blit back, so any colour format the GPU can blit to works.
/// False = not run (the format cannot be blitted to, or the strength is 0).
class Pass {
public:
    explicit Pass(const Instance& instance);
    ~Pass();

    bool Run(vk::CommandBuffer cmdbuf, vk::Image image, vk::Format format, vk::ImageLayout layout,
             u32 width, u32 height, int percent);

private:
    struct Images;
    const Instance& instance;
    vk::Device device;
    vk::UniqueSampler sampler;
    vk::UniqueDescriptorSetLayout set_layout;
    vk::UniquePipelineLayout pipeline_layout;
    vk::UniquePipeline pipeline;
    std::unique_ptr<Images> images;
    std::deque<std::pair<u64, std::unique_ptr<Images>>> retired; ///< freed 16 runs later
    u64 runs{};
    vk::Format blit_checked{vk::Format::eUndefined};
    bool blit_ok{};
};

} // namespace Vulkan::Sharpen
#endif
