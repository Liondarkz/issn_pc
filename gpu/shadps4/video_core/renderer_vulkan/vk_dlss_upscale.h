// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <atomic>
#include <deque>
#include <memory>
#include "common/types.h"
#include "video_core/renderer_vulkan/vk_common.h"
#include "video_core/renderer_vulkan/vk_dlss.h"
#include "video_core/texture_cache/image.h"

namespace Vulkan {
class Instance;
}

// SHADPS4_DLSS_UPSCALE=1 (with SHADPS4_DLSS=1; plan notes/study-dlss-plan-2026-10-14.md sec. 4,
// U1): the game renders 1920x1080 as always; DLSS outputs the window size (e.g. 2560x1440, the
// Quality mode range) into a host image instead of the T2x resolve's target. That target
// (0x26bd70000) is cleared to 0 and the HUD draws into it also write alpha, so it ends as a
// premultiplied HUD layer (they blend ONE / ONE_MINUS_SRC_ALPHA, measured). At the flip the
// presenter composites HUD over DLSS at the window size and its post pass draws that 1:1; the
// game's own final copy still runs (its display buffer, HUD over black, is not shown).
// Off when the window is not larger than 1920x1080 (then DLSS = DLAA in place) or the driver has
// no dynamic colour write mask.
namespace Vulkan::Dlss {

/// The resolve's target (0x26bd70000 at 1080p; learned per frame, vk_dlss.h "Any render size").
inline VAddr HudLayerAddr() {
    return g_hud_addr.load(std::memory_order_relaxed);
}

bool UpscaleWanted();

/// SHADPS4_DLSS_UPSCALE_TEST bits (crash bisect, 2026-10-17): 1 = no composite at the flip (the
/// display buffer is shown), 2 = no alpha on HUD draws, 4 = no clear of the resolve target.
u32 UpscaleTestBits();

/// The HUD-layer mode is on for this frame's draws (the presenter decides it each flip).
inline std::atomic<bool> g_hud_layer{false};

inline bool HudLayerActive() {
    return g_hud_layer.load(std::memory_order_relaxed) &&
           g_user_on.load(std::memory_order_relaxed);
}

/// Draw paths: a draw into the HUD layer that writes RGB writes RGBA (coverage in alpha).
template <typename Masks>
inline void ApplyHudLayerMask(Masks& masks, VAddr cb0_address) {
    constexpr vk::ColorComponentFlags rgb =
        vk::ColorComponentFlagBits::eR | vk::ColorComponentFlagBits::eG |
        vk::ColorComponentFlagBits::eB;
    if (cb0_address == HudLayerAddr() && masks[0] == rgb && HudLayerActive() &&
        !(UpscaleTestBits() & 2)) {
        masks[0] = rgb | vk::ColorComponentFlagBits::eA;
    }
}

/// What the presenter's recorded closure needs (plain handles; decided on the GPU thread).
struct CompositeJob {
    vk::Pipeline pipeline;
    vk::PipelineLayout layout;
    vk::ImageView hud_view;  ///< the HUD layer (game resolution), shader-read layout
    vk::ImageView scene_view; ///< DLSS output, shader-read layout
    vk::Image scene_image;    ///< its image (frame generation interpolates the HUD-free scene)
    vk::Image out_image;
    vk::ImageView out_view;
    vk::Extent2D size;
    bool hdr{};          ///< the game outputs HDR: the composite encodes PQ as its final pass does
    float hdr_scale{2.0f}; ///< that pass's paper white / 100 nits
};

/// ISSN_HDR_UPSCALE=0: the upscaler's window-size output stays off while the game outputs HDR (the
/// AA still runs at the render size), as before 11-16.
bool HdrUpscaleWanted();
/// Records the composite (barriers + dispatch) into `cmdbuf`; any thread.
void RecordComposite(vk::CommandBuffer cmdbuf, const CompositeJob& job);

class Upscaler {
public:
    explicit Upscaler(const Instance& instance);
    ~Upscaler();

    /// Presenter, each flip (GPU thread): the output size; turns the HUD-layer mode on / off.
    /// `hdr`: the game outputs HDR (BT.2020 PQ): the scene is kept in RGBA16F and the composite
    /// encodes PQ (11-16; ISSN_HDR_UPSCALE=0 keeps the HUD-layer mode off as before).
    void SetOutputSize(u32 width, u32 height, bool hdr = false);
    vk::Extent2D OutputSize() const {
        return output_size;
    }

    /// Resolve (GPU thread): the DLSS output image at the output size, moved to GENERAL for the
    /// evaluate with a barrier after the previous composite's read.
    EvalImage SceneTarget(vk::CommandBuffer cmdbuf);
    /// Resolve, after the evaluate: write -> read barrier; the next flip may composite.
    void SceneWritten(vk::CommandBuffer cmdbuf);

    /// Flip (GPU thread): this frame's DLSS output is waiting.
    bool Ready() const {
        return ready;
    }
    /// Flip: the job for this frame (and ready cleared). hud_view = the HUD layer.
    CompositeJob TakeJob(vk::ImageView hud_view);

private:
    struct Target {
        VideoCore::UniqueImage image;
        vk::UniqueImageView view;
    };
    void Resize(vk::Extent2D size);

    const Instance& instance;
    vk::Device device;
    vk::UniqueSampler sampler;
    vk::UniqueDescriptorSetLayout set_layout;
    vk::UniquePipelineLayout pipeline_layout;
    vk::UniquePipeline pipeline;
    std::unique_ptr<Target> scene; ///< DLSS output, RGBA8 UNORM (RGBA16F with HDR) (storage)
    std::unique_ptr<Target> out;   ///< composite, RGBA16F linear (PQ values with HDR)
    vk::ImageLayout scene_layout{vk::ImageLayout::eUndefined};
    vk::Extent2D output_size{};
    vk::Extent2D image_size{};
    bool hdr_mode{};  ///< the presenter's last frame was HDR
    bool image_hdr{}; ///< the scene image was made for HDR (RGBA16F)
    vk::Format SceneFormat() const {
        return image_hdr ? vk::Format::eR16G16B16A16Sfloat : vk::Format::eR8G8B8A8Unorm;
    }
    bool ready{};
    u64 flips{};
    std::deque<std::pair<u64, std::unique_ptr<Target>>> retired; ///< freed some flips later
};

/// The one upscaler (created on first use; nullptr when not wanted).
Upscaler* GetUpscaler(const Instance& instance);

} // namespace Vulkan::Dlss
