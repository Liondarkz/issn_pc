// SPDX-License-Identifier: GPL-2.0-or-later
// issn-axy: FSR 3.1 frame generation (ISSN_FRAME_GEN=1). From two finished frames of the game it
// makes the one in between, and the presenter shows it before the game's frame: 60 FPS on the
// screen become 120. AMD's FSR 3.1.6 frame interpolation through FireBurn/FSR-Vulkan (MIT, native
// Vulkan, any GPU). After the Bloodborne port's FrameGenerator (Supermedo's bloodborne_pc,
// vk_frame_generation.cpp, GPL-2.0-or-later); the inputs here are Second Son's own.
//
// Inputs: the depth and motion vectors are copied where the game's T2x resolve is replaced
// (Capture, from the DLSS / FSR 4 / XeSS slot, so frame generation needs one of those as
// anti-aliasing); the colour is the presenter's finished frame (Generate). Frames without a
// capture (menus, movies) are shown as they are.

#pragma once

#include <memory>
#include "common/types.h"
#include "video_core/renderer_vulkan/issn_fsr4.h"
#include "video_core/renderer_vulkan/vk_common.h"

namespace Vulkan {

class Instance;
class Scheduler;

namespace FrameGen {

/// ISSN_FRAME_GEN=1 (launcher: Performance > Frame generation).
bool Wanted();

/// Wanted and not switched off by a failure (the game's frames are then shown alone).
bool Active();

/// The rasterizer, where the resolve is replaced: this frame's depth (reversed Z, the depth
/// aspect is read) and motion vectors (screen UV, current minus previous), both in
/// TransferSrcOptimal layout, at the render size, and the frame's jitter in render pixels.
void Capture(const Instance& instance, Scheduler& scheduler, vk::CommandBuffer cmdbuf,
             const Fsr4Aa::Input& depth, const Fsr4Aa::Input& motion, float jitter_x,
             float jitter_y);

/// The presenter, after a frame was composed into `frame` (General layout, `width` x `height`,
/// the swapchain's SDR format): writes the frame between the previous one and this into `out`
/// (same size and format; left in General layout). Without a capture for this frame, or on a
/// failure, `out` gets a copy of `frame` (shown twice, as without generation). True when a frame
/// was generated.
bool Generate(const Instance& instance, Scheduler& scheduler, vk::CommandBuffer cmdbuf,
              vk::Image frame, vk::Image out, u32 width, u32 height);

/// Frees the context and images (the device is idle).
void Shutdown();

} // namespace FrameGen
} // namespace Vulkan
