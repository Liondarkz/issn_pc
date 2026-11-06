// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <atomic>
#include <cstdlib>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include "video_core/renderer_vulkan/liverpool_to_vk.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/texture_cache/sampler.h"

namespace VideoCore {

namespace {
std::atomic<float> g_forced_aniso{-1.0f}; ///< -1 = not read from ISSN_ANISO yet
}

float ForcedAniso() {
    float level = g_forced_aniso.load(std::memory_order_relaxed);
    if (level < 0.0f) {
        const char* v = std::getenv("ISSN_ANISO");
        level = v ? std::max(static_cast<float>(std::atof(v)), 0.0f) : 0.0f;
        float expected = -1.0f;
        g_forced_aniso.compare_exchange_strong(expected, level);
        level = g_forced_aniso.load(std::memory_order_relaxed);
    }
    return level;
}

void SetForcedAniso(float level) {
    g_forced_aniso.store(std::max(level, 0.0f), std::memory_order_relaxed);
}

Sampler::Sampler(const Vulkan::Instance& instance, const AmdGpu::Sampler& sampler,
                 const AmdGpu::BorderColorBuffer border_color_base) {
    using namespace Vulkan;
    // issn: ISSN_ANISO=4/8/16 (launcher: Graphics > Texture filtering) raises the anisotropy of the
    // game's filtered, mipmapped colour samplers to at least that (sharper ground and walls at an
    // angle, like a PC game's setting); point, unmipmapped and depth-compare samplers stay as the
    // game set them. Unset or 1: the game's own filtering.
    const float forced_aniso = ForcedAniso();
    const bool force_aniso = forced_aniso > 1.0f &&
                             sampler.xy_min_filter != AmdGpu::Filter::Point &&
                             sampler.xy_min_filter != AmdGpu::Filter::AnisoPoint &&
                             sampler.mip_filter != AmdGpu::MipFilter::None &&
                             sampler.depth_compare_func == AmdGpu::DepthCompare::Never &&
                             // an aniso sampler cut to 1x (BindTextures: a texture without mips)
                             !(AmdGpu::IsAnisoFilter(sampler.xy_min_filter) &&
                               sampler.max_aniso.Value() == AmdGpu::AnisoRatio::One);
    const bool anisotropy_enable = instance.IsAnisotropicFilteringSupported() &&
                                   (AmdGpu::IsAnisoFilter(sampler.xy_mag_filter) ||
                                    AmdGpu::IsAnisoFilter(sampler.xy_min_filter) || force_aniso);
    const float max_anisotropy =
        anisotropy_enable
            ? std::clamp(std::max(sampler.MaxAniso(), force_aniso ? forced_aniso : 1.0f), 1.0f,
                         instance.MaxSamplerAnisotropy())
            : 1.0f;
    auto border_color = LiverpoolToVK::BorderColor(sampler.border_color_type);
    if (border_color == vk::BorderColor::eFloatCustomEXT &&
        !instance.IsCustomBorderColorSupported()) {
        LOG_WARNING(Render_Vulkan, "Custom border color is not supported, falling back to black");
        border_color = vk::BorderColor::eFloatOpaqueBlack;
    }

    auto custom_color = [&]() -> std::optional<vk::SamplerCustomBorderColorCreateInfoEXT> {
        if (border_color == vk::BorderColor::eFloatCustomEXT) {
            const auto border_color_index = sampler.border_color_ptr.Value();
            const auto border_color_buffer = border_color_base.Address<std::array<float, 4>*>();
            const auto custom_border_color_array = border_color_buffer[border_color_index];

            const vk::SamplerCustomBorderColorCreateInfoEXT ret{
                .customBorderColor =
                    vk::ClearColorValue{
                        .float32 = custom_border_color_array,
                    },
                .format = vk::Format::eR32G32B32A32Sfloat,
            };
            return ret;
        } else {
            return std::nullopt;
        }
    }();

    // Descriptor heaps take custom border colours by registered index. Never substitute a
    // different colour if registration fails: that would silently alter native-mode rendering.
    auto* native_bindings = instance.IsNativeBindingsActive() ? NativeBindings::Get() : nullptr;
    NativeApi::SamplerCustomBorderColorIndexCreateInfo border_index_ci = {
        .sType = NativeApi::StructureTypeSamplerCustomBorderColorIndexCreateInfo,
    };
    if (native_bindings && custom_color) {
        if (native_bindings->RegisterBorderColor(*custom_color, native.border_index)) {
            native.border_registered = true;
            border_index_ci.index = native.border_index;
            custom_color->pNext = &border_index_ci;
        } else {
            UNREACHABLE_MSG("NativeBindings: custom border color registration failed");
        }
    }

    // SHADPS4_TEX_LOD_BIAS=-1.5 (test, 10-20): added to every sampler's mip bias - DLSS's advice
    // for renders below the output size (log2(render / output)).
    // issn 11-06 (João tested teste-mip-bias.bat, -0.58 with FSR 4 Quality: sharper): automatic
    // when an upscaler draws below the window (ISSN_UPSCALE_FACTOR, Quality 1.5 ... 3), so the
    // textures keep the detail of the output size: -log2(factor). ISSN_TEX_LOD_AUTO=0 turns it off;
    // SHADPS4_TEX_LOD_BIAS still wins.
    static const float extra_bias = [] {
        if (const char* v = std::getenv("SHADPS4_TEX_LOD_BIAS"); v && *v) {
            return static_cast<float>(std::atof(v));
        }
        const char* autov = std::getenv("ISSN_TEX_LOD_AUTO");
        const char* f = std::getenv("ISSN_UPSCALE_FACTOR");
        const double factor = f ? std::atof(f) : 0.0;
        float bias = 0.0f;
        if (!(autov && autov[0] == '0') && factor > 1.01 && factor <= 4.0) {
            bias = -static_cast<float>(std::log2(factor));
        }
        std::printf("Texture mip bias: %.2f (%s)\n", bias,
                    bias != 0.0f ? "the upscaler draws below the window; ISSN_TEX_LOD_AUTO=0 turns it off"
                                 : "no upscaler below the window size");
        std::fflush(stdout);
        return bias;
    }();
    const vk::SamplerCreateInfo sampler_ci = {
        .pNext = custom_color ? &*custom_color : nullptr,
        .magFilter = LiverpoolToVK::Filter(sampler.xy_mag_filter),
        .minFilter = LiverpoolToVK::Filter(sampler.xy_min_filter),
        .mipmapMode = LiverpoolToVK::MipFilter(sampler.mip_filter),
        .addressModeU = LiverpoolToVK::ClampMode(sampler.clamp_x),
        .addressModeV = LiverpoolToVK::ClampMode(sampler.clamp_y),
        .addressModeW = LiverpoolToVK::ClampMode(sampler.clamp_z),
        .mipLodBias = std::clamp(sampler.LodBias() + extra_bias, -instance.MaxSamplerLodBias(),
                                 instance.MaxSamplerLodBias()),
        .anisotropyEnable = anisotropy_enable,
        .maxAnisotropy = max_anisotropy,
        .compareEnable = sampler.depth_compare_func != AmdGpu::DepthCompare::Never,
        .compareOp = LiverpoolToVK::DepthCompare(sampler.depth_compare_func),
        .minLod = sampler.MinLod(),
        .maxLod = sampler.MaxLod(),
        .borderColor = border_color,
        .unnormalizedCoordinates = false, // Handled in shader due to Vulkan limitations.
    };
    auto [sampler_result, smplr] = instance.GetDevice().createSamplerUnique(sampler_ci);
    ASSERT_MSG(sampler_result == vk::Result::eSuccess, "Failed to create sampler: {}",
               vk::to_string(sampler_result));
    handle = std::move(smplr);

    if (native_bindings) {
        native.sampler_ci = sampler_ci;
        native.sampler_ci.pNext = nullptr;
        if (custom_color) {
            native.custom_color = *custom_color;
            native.custom_color.pNext = nullptr;
            native.has_custom_color = true;
        }
        native.armed = true;
    }
}

Sampler::~Sampler() = default;

} // namespace VideoCore
