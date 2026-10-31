// SPDX-License-Identifier: GPL-2.0-or-later
// issn-axy: FSR 3.1 frame generation (issn_framegen.h).
#include "video_core/renderer_vulkan/issn_framegen.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <utility>

#include "common/logging/log.h"
#include "ffx_vk_fsr3_3_1_5_bridge.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/host_shaders/framegen_depth_comp.h"
#include "video_core/renderer_vulkan/vk_dlss.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_platform.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/renderer_vulkan/vk_shader_util.h"
#include "video_core/texture_cache/image.h"

namespace Vulkan::FrameGen {

namespace {

constexpr vk::Format ColorFormat = vk::Format::eR8G8B8A8Unorm; // FSR 3.1's SDR colour
// FFX_FRAMEINTERPOLATION_ENABLE_DEPTH_INVERTED would match the game's reversed depth, but the
// bridge embeds only the shader variants of the default (10-30: creating the context with it
// failed, FFX_ERROR_INVALID_ARGUMENT at the pipeline bindings). The Bloodborne port passes its
// reversed depth the same way; the interpolation then judges near and far the other way round
// at object edges only.
constexpr u32 InterpolationFlags = 0u;

constexpr vk::ImageSubresourceRange ColorRange{
    .aspectMask = vk::ImageAspectFlagBits::eColor,
    .levelCount = 1,
    .layerCount = 1,
};
constexpr vk::ImageSubresourceLayers ColorLayers{
    .aspectMask = vk::ImageAspectFlagBits::eColor,
    .mipLevel = 0,
    .baseArrayLayer = 0,
    .layerCount = 1,
};
constexpr vk::ImageUsageFlags Usage = vk::ImageUsageFlagBits::eSampled |
                                      vk::ImageUsageFlagBits::eStorage |
                                      vk::ImageUsageFlagBits::eTransferSrc |
                                      vk::ImageUsageFlagBits::eTransferDst;

std::atomic<bool> g_failed{false};
std::atomic<u64> g_generated{0}; ///< GeneratedCount
std::atomic<int> g_enabled{-1};  ///< the menu's switch; -1 = not read from ISSN_FRAME_GEN yet
std::atomic<bool> g_reset_request{false}; ///< switched on again: start a new history

struct Owned {
    VideoCore::UniqueImage image;
    vk::UniqueImageView view; ///< destroyed before the image (declared after it)
    vk::ImageLayout layout = vk::ImageLayout::eUndefined;
    vk::Format format = vk::Format::eUndefined;
    u32 width = 0, height = 0;
};

void To(vk::CommandBuffer cmdbuf, Owned& owned, vk::ImageLayout layout) {
    cmdbuf.pipelineBarrier(
        vk::PipelineStageFlagBits::eAllCommands, vk::PipelineStageFlagBits::eAllCommands, {}, {},
        {},
        vk::ImageMemoryBarrier{
            .srcAccessMask = vk::AccessFlagBits::eMemoryWrite | vk::AccessFlagBits::eMemoryRead,
            .dstAccessMask = vk::AccessFlagBits::eMemoryWrite | vk::AccessFlagBits::eMemoryRead,
            .oldLayout = owned.layout,
            .newLayout = layout,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image = owned.image,
            .subresourceRange = ColorRange,
        });
    owned.layout = layout;
}

void Barrier(vk::CommandBuffer cmdbuf, vk::Image image, vk::ImageLayout from, vk::ImageLayout to) {
    cmdbuf.pipelineBarrier(
        vk::PipelineStageFlagBits::eAllCommands, vk::PipelineStageFlagBits::eAllCommands, {}, {},
        {},
        vk::ImageMemoryBarrier{
            .srcAccessMask = vk::AccessFlagBits::eMemoryWrite | vk::AccessFlagBits::eMemoryRead,
            .dstAccessMask = vk::AccessFlagBits::eMemoryWrite | vk::AccessFlagBits::eMemoryRead,
            .oldLayout = from,
            .newLayout = to,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image = image,
            .subresourceRange = ColorRange,
        });
}

void Blit(vk::CommandBuffer cmdbuf, vk::Image source, vk::ImageLayout source_layout, u32 sw,
          u32 sh, vk::Image dest, u32 dw, u32 dh) {
    const vk::ImageBlit blit{
        .srcSubresource = ColorLayers,
        .srcOffsets = std::array{vk::Offset3D{0, 0, 0}, vk::Offset3D{s32(sw), s32(sh), 1}},
        .dstSubresource = ColorLayers,
        .dstOffsets = std::array{vk::Offset3D{0, 0, 0}, vk::Offset3D{s32(dw), s32(dh), 1}},
    };
    cmdbuf.blitImage(source, source_layout, dest, vk::ImageLayout::eTransferDstOptimal, blit,
                     vk::Filter::eNearest);
}

struct State {
    const Instance& instance;
    Scheduler& scheduler;
    std::mutex mutex;
    Owned depth, motion;  ///< this frame's capture (render size)
    Owned color, output;  ///< the finished frame and the generated one (display size)
    Owned composite;      ///< the generated scene with the HUD over it (HUD-free path)
    std::unique_ptr<VideoCore::UniqueBuffer> depth_buffer;
    u64 depth_buffer_size = 0;
    std::deque<std::pair<u64, Owned>> retired_images;
    std::deque<std::pair<u64, std::unique_ptr<VideoCore::UniqueBuffer>>> retired_buffers;
    bool captured = false;                 ///< a capture since the last TakeCapture
    float jitter_x = 0.0f, jitter_y = 0.0f; ///< its jitter
    Ticket ticket;                         ///< the flip being generated (Interpolate)
    vk::UniqueDescriptorSetLayout depth_set_layout;
    vk::UniquePipelineLayout depth_layout;
    vk::UniquePipeline depth_pipeline;
    bool depth_pipeline_failed = false;
    FfxVkFsr3_3_1_6FrameGenerationContext* context = nullptr;
    u32 ctx_width = 0, ctx_height = 0, ctx_render_w = 0, ctx_render_h = 0;
    u64 frame_id = 0;
    std::deque<std::pair<u64, u64>> pending; ///< frame id, scheduler tick
    bool reset = true;
    u32 misses = 0; ///< flips in a row without a capture
    std::chrono::steady_clock::time_point last_generate{};
    u64 generated = 0, copied = 0;
    std::chrono::steady_clock::time_point last_summary{};
    u64 summary_generated = 0, summary_copied = 0;
    std::string problem;

    State(const Instance& instance_, Scheduler& scheduler_)
        : instance{instance_}, scheduler{scheduler_} {}

    void Ensure(Owned& owned, vk::Format format, u32 width, u32 height) {
        if (owned.image && owned.format == format && owned.width == width &&
            owned.height == height) {
            return;
        }
        if (owned.image) {
            retired_images.emplace_back(scheduler.CurrentTick(), std::move(owned));
        }
        owned.image = VideoCore::UniqueImage(instance.GetDevice(), instance.GetAllocator());
        owned.image.Create(vk::ImageCreateInfo{
            .imageType = vk::ImageType::e2D,
            .format = format,
            .extent{width, height, 1},
            .mipLevels = 1,
            .arrayLayers = 1,
            .samples = vk::SampleCountFlagBits::e1,
            .usage = Usage,
            .initialLayout = vk::ImageLayout::eUndefined,
        });
        owned.view = Check<"create frame generation view">(
            instance.GetDevice().createImageViewUnique(vk::ImageViewCreateInfo{
                .image = owned.image,
                .viewType = vk::ImageViewType::e2D,
                .format = format,
                .subresourceRange = ColorRange,
            }));
        owned.layout = vk::ImageLayout::eUndefined;
        owned.format = format;
        owned.width = width;
        owned.height = height;
    }

    void Retire() {
        while (context && !pending.empty() && scheduler.IsFree(pending.front().second)) {
            ffxVkFsr3_3_1_6FrameGenerationContextRetireFrame(context, pending.front().first);
            pending.pop_front();
        }
        while (!retired_images.empty() && scheduler.IsFree(retired_images.front().first)) {
            retired_images.pop_front();
        }
        while (!retired_buffers.empty() && scheduler.IsFree(retired_buffers.front().first)) {
            retired_buffers.pop_front();
        }
    }

    void DestroyContext() {
        if (!context) {
            return;
        }
        // A size change (rare): the old context's submitted frames must be done. Only the
        // device is waited for: this runs while a command buffer is being recorded.
        (void)instance.GetDevice().waitIdle();
        ffxVkFsr3_3_1_6FrameGenerationContextRetireFrame(context, frame_id);
        ffxVkFsr3_3_1_6FrameGenerationContextDestroy(context);
        context = nullptr;
        pending.clear();
    }

    void Fail(std::string reason) {
        if (problem != reason) {
            LOG_WARNING(Render_Vulkan, "Frame generation off: {}", reason);
            std::printf("Frame generation: off (%s)\n", reason.c_str());
            std::fflush(stdout);
        }
        problem = std::move(reason);
        g_failed.store(true, std::memory_order_relaxed);
    }

    void Capture(vk::CommandBuffer cmdbuf, const Fsr4Aa::Input& d, const Fsr4Aa::Input& m, float jx,
                 float jy) {
        Retire();
        Ensure(depth, vk::Format::eR32Sfloat, d.width, d.height);
        Ensure(motion, vk::Format::eR16G16Sfloat, m.width, m.height);
        const u64 depth_bytes = u64(d.width) * d.height * 4;
        if (!depth_buffer || depth_buffer_size < depth_bytes) {
            if (depth_buffer) {
                retired_buffers.emplace_back(scheduler.CurrentTick(), std::move(depth_buffer));
            }
            depth_buffer = std::make_unique<VideoCore::UniqueBuffer>(instance.GetDevice(),
                                                                     instance.GetAllocator());
            depth_buffer->Create(vk::BufferCreateInfo{
                                     .size = depth_bytes,
                                     .usage = vk::BufferUsageFlagBits::eTransferSrc |
                                              vk::BufferUsageFlagBits::eTransferDst,
                                 },
                                 VideoCore::MemoryUsage::DeviceLocal, nullptr);
            depth_buffer_size = depth_bytes;
        }
        To(cmdbuf, depth, vk::ImageLayout::eTransferDstOptimal);
        To(cmdbuf, motion, vk::ImageLayout::eTransferDstOptimal);
        Blit(cmdbuf, m.image, vk::ImageLayout::eTransferSrcOptimal, m.width, m.height,
             motion.image, m.width, m.height);
        // The depth aspect of D32S8 goes through a buffer into R32F (as FSR 4's CopyInputs).
        const vk::BufferImageCopy depth_copy{
            .bufferOffset = 0,
            .bufferRowLength = 0,
            .bufferImageHeight = 0,
            .imageSubresource{
                .aspectMask = vk::ImageAspectFlagBits::eDepth,
                .mipLevel = 0,
                .baseArrayLayer = 0,
                .layerCount = 1,
            },
            .imageOffset{0, 0, 0},
            .imageExtent{d.width, d.height, 1},
        };
        cmdbuf.copyImageToBuffer(d.image, vk::ImageLayout::eTransferSrcOptimal, *depth_buffer,
                                 depth_copy);
        cmdbuf.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer,
                               vk::PipelineStageFlagBits::eTransfer, {},
                               vk::MemoryBarrier{
                                   .srcAccessMask = vk::AccessFlagBits::eTransferWrite,
                                   .dstAccessMask = vk::AccessFlagBits::eTransferRead,
                               },
                               {}, {});
        vk::BufferImageCopy colour_copy = depth_copy;
        colour_copy.imageSubresource.aspectMask = vk::ImageAspectFlagBits::eColor;
        cmdbuf.copyBufferToImage(*depth_buffer, depth.image, vk::ImageLayout::eTransferDstOptimal,
                                 colour_copy);
        To(cmdbuf, depth, vk::ImageLayout::eGeneral);
        To(cmdbuf, motion, vk::ImageLayout::eGeneral);
        jitter_x = jx;
        jitter_y = jy;
        captured = true;
    }

    /// The captured depth turned round in place (framegen_depth.comp). ISSN_FRAME_GEN_DEPTH=raw
    /// leaves it as the game wrote it (to compare).
    void InvertDepth(vk::CommandBuffer cmdbuf) {
        static const bool raw = [] {
            const char* v = std::getenv("ISSN_FRAME_GEN_DEPTH");
            return v && std::string_view{v} == "raw";
        }();
        if (raw || depth_pipeline_failed) {
            return;
        }
        const vk::Device device = instance.GetDevice();
        if (!depth_pipeline) {
            const vk::DescriptorSetLayoutBinding binding{
                .binding = 0,
                .descriptorType = vk::DescriptorType::eStorageImage,
                .descriptorCount = 1,
                .stageFlags = vk::ShaderStageFlagBits::eCompute,
            };
            depth_set_layout = Check<"create frame generation depth set layout">(
                device.createDescriptorSetLayoutUnique({
                    .flags = vk::DescriptorSetLayoutCreateFlagBits::ePushDescriptor,
                    .bindingCount = 1,
                    .pBindings = &binding,
                }));
            depth_layout = Check<"create frame generation depth layout">(
                device.createPipelineLayoutUnique({
                    .setLayoutCount = 1,
                    .pSetLayouts = &depth_set_layout.get(),
                }));
            const auto module =
                Compile(HostShaders::FRAMEGEN_DEPTH_COMP, vk::ShaderStageFlagBits::eCompute, device);
            if (!module) {
                depth_pipeline_failed = true;
                std::printf("Frame generation: the depth pass did not compile; depth goes as the "
                            "game wrote it\n");
                std::fflush(stdout);
                return;
            }
            depth_pipeline = Check<"create frame generation depth pipeline">(
                device.createComputePipelineUnique(
                    {}, vk::ComputePipelineCreateInfo{
                            .stage{
                                .stage = vk::ShaderStageFlagBits::eCompute,
                                .module = module,
                                .pName = "main",
                            },
                            .layout = depth_layout.get(),
                        }));
            device.destroyShaderModule(module);
        }
        To(cmdbuf, depth, vk::ImageLayout::eGeneral);
        const vk::DescriptorImageInfo info{.imageView = *depth.view,
                                           .imageLayout = vk::ImageLayout::eGeneral};
        const vk::WriteDescriptorSet write{
            .dstBinding = 0,
            .descriptorCount = 1,
            .descriptorType = vk::DescriptorType::eStorageImage,
            .pImageInfo = &info,
        };
        cmdbuf.bindPipeline(vk::PipelineBindPoint::eCompute, *depth_pipeline);
        cmdbuf.pushDescriptorSetKHR(vk::PipelineBindPoint::eCompute, *depth_layout, 0, write);
        cmdbuf.dispatch((depth.width + 7) / 8, (depth.height + 7) / 8, 1);
        To(cmdbuf, depth, vk::ImageLayout::eGeneral); // write -> FSR's reads
    }

    bool Interpolate(vk::CommandBuffer cmdbuf, u32 width, u32 height, float frame_ms) {
        if (g_reset_request.exchange(false, std::memory_order_relaxed)) {
            reset = true; // switched on again from the menu: no history across the gap
        }
        if (context && (ctx_width != width || ctx_height != height ||
                        ctx_render_w != depth.width || ctx_render_h != depth.height)) {
            DestroyContext();
        }
        if (!context) {
            const FfxVkFsr3_3_1_6FrameGenerationCreateInfo create{
                .physicalDevice = instance.GetPhysicalDevice(),
                .device = instance.GetDevice(),
                .maxRenderWidth = depth.width,
                .maxRenderHeight = depth.height,
                .displayWidth = width,
                .displayHeight = height,
                .colorFormat = static_cast<VkFormat>(ColorFormat),
                .interpolationFlags = InterpolationFlags,
            };
            if (ffxVkFsr3_3_1_6FrameGenerationContextCreate(&create, &context) !=
                FFX_VK_FSR3_3_1_6_FRAMEGEN_OK) {
                context = nullptr;
                Fail("FSR 3.1 context creation failed");
                return false;
            }
            ctx_width = width;
            ctx_height = height;
            ctx_render_w = depth.width;
            ctx_render_h = depth.height;
            reset = true;
            FfxVkFsr3_3_1_6FrameGenerationMemoryUsage usage{};
            ffxVkFsr3_3_1_6FrameGenerationContextGetMemoryUsage(context, &usage);
            LOG_WARNING(Render_Vulkan,
                        "Frame generation: FSR 3.1 at {}x{} (depth and motion {}x{}), {:.1f} MB",
                        width, height, depth.width, depth.height, usage.totalUsageInBytes / 1e6);
            std::printf("Frame generation: FSR 3.1, %ux%u\n", width, height);
            std::fflush(stdout);
        }
        const auto image = [](const Owned& owned) {
            return FfxVkFsr3_3_1_6FrameGenerationImage{
                .image = static_cast<VkImage>(static_cast<vk::Image>(owned.image)),
                .format = static_cast<VkFormat>(owned.format),
                .width = owned.width,
                .height = owned.height,
                .layout = VK_IMAGE_LAYOUT_GENERAL,
                .usage = static_cast<VkImageUsageFlags>(Usage),
            };
        };
        // The game's vectors are screen UV, current minus previous; FSR wants render pixels
        // towards the previous position (as FSR 4). ISSN_FRAME_GEN_MV=+ flips the sign.
        static const float mv_sign = [] {
            const char* v = std::getenv("ISSN_FRAME_GEN_MV");
            return v && v[0] == '+' ? 1.0f : -1.0f;
        }();
        InvertDepth(cmdbuf);
        const u64 id = ++frame_id;
        const FfxVkFsr3_3_1_6FrameGenerationPrepareInfo prepare{
            .commandBuffer = cmdbuf,
            .color = image(color),
            .depth = image(depth),
            .motionVectors = image(motion),
            .renderWidth = depth.width,
            .renderHeight = depth.height,
            .jitterOffsetX = ticket.jitter_x,
            .jitterOffsetY = ticket.jitter_y,
            .motionVectorScaleX = mv_sign * float(motion.width),
            .motionVectorScaleY = mv_sign * float(motion.height),
            .frameTimeMilliseconds = frame_ms,
            .minLuminance = 0.0f,
            .maxLuminance = 1.0f,
            .transferFunction = FFX_VK_FSR3_3_1_6_FRAMEGEN_TRANSFER_SRGB,
            .cameraNear = 0.1f,
            .cameraFar = 10000.0f,
            .viewSpaceToMeters = 1.0f,
            .cameraVerticalFovRadians = 1.0f,
            .cameraPosition = {0.0f, 0.0f, 0.0f},
            .cameraUp = {0.0f, 1.0f, 0.0f},
            .cameraRight = {1.0f, 0.0f, 0.0f},
            .cameraForward = {0.0f, 0.0f, 1.0f},
            .frameId = id,
            .reset = reset ? VK_TRUE : VK_FALSE,
        };
        if (ffxVkFsr3_3_1_6FrameGenerationContextRecordPrepare(context, &prepare) !=
            FFX_VK_FSR3_3_1_6_FRAMEGEN_OK) {
            pending.emplace_back(id, scheduler.CurrentTick());
            Fail("FSR 3.1 prepare failed");
            return false;
        }
        const FfxVkFsr3_3_1_6FrameGenerationDispatchInfo dispatch{
            .commandBuffer = cmdbuf,
            .color = prepare.color,
            .output = image(output),
            .distortionField = {},
            .displayWidth = width,
            .displayHeight = height,
            .frameTimeMilliseconds = frame_ms,
            .cameraNear = prepare.cameraNear,
            .cameraFar = prepare.cameraFar,
            .viewSpaceToMeters = prepare.viewSpaceToMeters,
            .cameraVerticalFovRadians = prepare.cameraVerticalFovRadians,
            .minLuminance = 0.0f,
            .maxLuminance = 1.0f,
            .transferFunction = FFX_VK_FSR3_3_1_6_FRAMEGEN_TRANSFER_SRGB,
            .frameId = id,
            .reset = prepare.reset,
        };
        const auto result = ffxVkFsr3_3_1_6FrameGenerationContextRecordDispatch(context, &dispatch);
        pending.emplace_back(id, scheduler.CurrentTick());
        if (result != FFX_VK_FSR3_3_1_6_FRAMEGEN_OK) {
            Fail("FSR 3.1 dispatch failed");
            return false;
        }
        return true;
    }

    float FrameMs() {
        const auto now = std::chrono::steady_clock::now();
        float frame_ms = std::chrono::duration<float, std::milli>(now - last_generate).count();
        last_generate = now;
        return frame_ms > 1.0f && frame_ms < 200.0f ? frame_ms : 16.6f;
    }

    void Tell(bool have_capture, bool hudless) {
        // The log says once whether the inputs arrive (10-30: a test showed no generated frames
        // and the log said nothing about frame generation) and which path the frames take.
        // Each message once (10-30: flips with and without a capture alternate while playing,
        // and the change was printed every time); Count sums them up once a minute.
        static bool told[3] = {};
        const int now = have_capture ? (hudless ? 2 : 1) : 0;
        if (!told[now]) {
            told[now] = true;
            std::printf("Frame generation: %s\n",
                        now == 2   ? "depth and motion vectors arrive, interpolating the scene "
                                     "(the HUD is laid over it sharp)"
                        : now == 1 ? "depth and motion vectors arrive, interpolating the whole "
                                     "frame (no HUD layer: the HUD can break up in motion)"
                                   : "no depth / motion capture for this frame (menu, movie, or "
                                     "the anti-aliasing is not FSR 4 / XeSS / DLSS): shown as is");
            std::fflush(stdout);
        }
    }

    void Count(bool generated) {
        ++(generated ? this->generated : copied);
        if (generated) {
            g_generated.fetch_add(1, std::memory_order_relaxed);
        }
        const auto now = std::chrono::steady_clock::now();
        if (now - last_summary >= std::chrono::seconds(60)) {
            if (last_summary != std::chrono::steady_clock::time_point{}) {
                std::printf("Frame generation: last minute %llu frames generated, %llu flips "
                            "without a new frame to interpolate\n",
                            static_cast<unsigned long long>(this->generated - summary_generated),
                            static_cast<unsigned long long>(copied - summary_copied));
                std::fflush(stdout);
            }
            last_summary = now;
            summary_generated = this->generated;
            summary_copied = copied;
        }
    }

    Ticket TakeCapture() {
        Ticket taken{captured, jitter_x, jitter_y};
        captured = false;
        return taken;
    }

    std::optional<SceneFrame> GenerateScene(vk::CommandBuffer cmdbuf, const Ticket& flip,
                                            vk::Image scene, u32 width, u32 height) {
        if (!flip.captured || g_failed.load(std::memory_order_relaxed)) {
            return std::nullopt; // Generate copies the finished frame (and says why)
        }
        Retire();
        const float frame_ms = FrameMs();
        ticket = flip;
        Tell(true, true);
        Ensure(color, ColorFormat, width, height);
        Ensure(output, ColorFormat, width, height);
        Ensure(composite, vk::Format::eR16G16B16A16Sfloat, width, height);
        // The scene (the upscaler's output, this frame's HUD not in it) into FSR's colour image;
        // the scene goes back to the layout the upscaler keeps track of.
        Barrier(cmdbuf, scene, vk::ImageLayout::eShaderReadOnlyOptimal,
                vk::ImageLayout::eTransferSrcOptimal);
        To(cmdbuf, color, vk::ImageLayout::eTransferDstOptimal);
        Blit(cmdbuf, scene, vk::ImageLayout::eTransferSrcOptimal, width, height, color.image, width,
             height);
        Barrier(cmdbuf, scene, vk::ImageLayout::eTransferSrcOptimal,
                vk::ImageLayout::eShaderReadOnlyOptimal);
        To(cmdbuf, color, vk::ImageLayout::eGeneral);
        To(cmdbuf, output, vk::ImageLayout::eGeneral);
        if (!Interpolate(cmdbuf, width, height, frame_ms)) {
            reset = true;
            return std::nullopt;
        }
        reset = false;
        misses = 0;
        To(cmdbuf, output, vk::ImageLayout::eShaderReadOnlyOptimal);
        Count(true);
        return SceneFrame{
            .scene = *output.view,
            .composite_image = composite.image,
            .composite_view = *composite.view,
        };
    }

    bool Generate(vk::CommandBuffer cmdbuf, const Ticket& flip, vk::Image frame, vk::Image out,
                  u32 width, u32 height) {
        Retire();
        const bool have_capture = flip.captured;
        const float frame_ms = FrameMs();
        ticket = flip;
        Tell(have_capture, false);
        // The composed frame (the post pass wrote it) into FSR's colour image.
        Barrier(cmdbuf, frame, vk::ImageLayout::eGeneral, vk::ImageLayout::eGeneral);
        Barrier(cmdbuf, out, vk::ImageLayout::eUndefined, vk::ImageLayout::eTransferDstOptimal);
        bool generated = false;
        if (have_capture && !g_failed.load(std::memory_order_relaxed)) {
            Ensure(color, ColorFormat, width, height);
            Ensure(output, ColorFormat, width, height);
            To(cmdbuf, color, vk::ImageLayout::eTransferDstOptimal);
            Blit(cmdbuf, frame, vk::ImageLayout::eGeneral, width, height, color.image, width,
                 height);
            To(cmdbuf, color, vk::ImageLayout::eGeneral);
            To(cmdbuf, output, vk::ImageLayout::eGeneral);
            generated = Interpolate(cmdbuf, width, height, frame_ms);
            if (generated) {
                reset = false;
                misses = 0;
                To(cmdbuf, output, vk::ImageLayout::eTransferSrcOptimal);
                Blit(cmdbuf, output.image, vk::ImageLayout::eTransferSrcOptimal, width, height,
                     out, width, height);
            }
        }
        if (!generated) {
            // No inputs this frame or a failure: the frame itself. The next generation starts a
            // new history only after a few such flips (a menu, a movie): the game also flips a
            // frame now and then without rendering a new one (10-30 log: captures and flips
            // without one alternating while playing), and a reset there made the next
            // interpolated frame start from nothing.
            if (++misses >= 4 || !have_capture && g_failed.load(std::memory_order_relaxed)) {
                reset = true;
            }
            Blit(cmdbuf, frame, vk::ImageLayout::eGeneral, width, height, out, width, height);
        }
        Barrier(cmdbuf, out, vk::ImageLayout::eTransferDstOptimal, vk::ImageLayout::eGeneral);
        Count(generated);
        return generated;
    }
};

std::mutex g_state_mutex;
std::unique_ptr<State> g_state;

State& Get(const Instance& instance, Scheduler& scheduler) {
    std::scoped_lock lock{g_state_mutex};
    if (!g_state) {
        g_state = std::make_unique<State>(instance, scheduler);
    }
    return *g_state;
}

} // namespace

bool Wanted() {
    static const bool capable = [] {
        const int mode = Dlss::Mode();
        const char* off = std::getenv("ISSN_FRAME_GEN");
        // ISSN_FRAME_GEN=off: not even available from the menu (fewer presentation frames).
        return (mode == 1 || mode == 3 || mode == 4) && !(off && std::string_view{off} == "off");
    }();
    return capable;
}

bool Enabled() {
    int on = g_enabled.load(std::memory_order_relaxed);
    if (on < 0) {
        const char* v = std::getenv("ISSN_FRAME_GEN");
        on = v && v[0] == '1' ? 1 : 0;
        int expected = -1;
        g_enabled.compare_exchange_strong(expected, on);
        on = g_enabled.load(std::memory_order_relaxed);
    }
    return on == 1;
}

void SetEnabled(bool on) {
    if (Enabled() == on) {
        return;
    }
    g_enabled.store(on ? 1 : 0, std::memory_order_relaxed);
    if (on) {
        g_reset_request.store(true, std::memory_order_relaxed);
    }
    std::printf("Frame generation: %s from the menu\n", on ? "on" : "off");
    std::fflush(stdout);
}

bool Active() {
    return Wanted() && Enabled() && !g_failed.load(std::memory_order_relaxed);
}

void Capture(const Instance& instance, Scheduler& scheduler, vk::CommandBuffer cmdbuf,
             const Fsr4Aa::Input& depth, const Fsr4Aa::Input& motion, float jitter_x,
             float jitter_y) {
    if (!Active()) {
        return;
    }
    State& state = Get(instance, scheduler);
    std::scoped_lock lock{state.mutex};
    state.Capture(cmdbuf, depth, motion, jitter_x, jitter_y);
}

Ticket TakeCapture() {
    std::scoped_lock lock{g_state_mutex};
    if (!g_state) {
        return {};
    }
    std::scoped_lock state_lock{g_state->mutex};
    return g_state->TakeCapture();
}

bool Generate(const Instance& instance, Scheduler& scheduler, vk::CommandBuffer cmdbuf,
              const Ticket& ticket, vk::Image frame, vk::Image out, u32 width, u32 height) {
    State& state = Get(instance, scheduler);
    std::scoped_lock lock{state.mutex};
    return state.Generate(cmdbuf, ticket, frame, out, width, height);
}

std::optional<SceneFrame> GenerateScene(const Instance& instance, Scheduler& scheduler,
                                        vk::CommandBuffer cmdbuf, const Ticket& ticket,
                                        vk::Image scene, u32 width, u32 height) {
    State& state = Get(instance, scheduler);
    std::scoped_lock lock{state.mutex};
    return state.GenerateScene(cmdbuf, ticket, scene, width, height);
}

u64 GeneratedCount() {
    return g_generated.load(std::memory_order_relaxed);
}

void Shutdown() {
    std::scoped_lock lock{g_state_mutex};
    if (g_state) {
        std::scoped_lock state_lock{g_state->mutex};
        g_state->DestroyContext();
        g_state.reset();
    }
}

} // namespace Vulkan::FrameGen
