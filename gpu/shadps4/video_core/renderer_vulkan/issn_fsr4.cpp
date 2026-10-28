// SPDX-License-Identifier: GPL-2.0-or-later
// issn-axy: FSR 4 Native AA in the T2x resolve's place (issn_fsr4.h). Ported from the port's
// previous core (vk_fsr4.cpp of branch issn), without its 4.1.1 replay.
#include "video_core/renderer_vulkan/issn_fsr4.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <fstream>
#include <utility>
#include <vector>

#include "common/logging/log.h"
#include "ffx_vk_fsr4_v07.h"
#include "ffx_vk_fsr4_v07_assets.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/texture_cache/image.h"

namespace Vulkan::Fsr4Aa {

namespace {

std::atomic<bool> g_device_supported{false};

std::string AssetDir() {
    const char* dir = std::getenv("ISSN_FSR4_DIR");
    return dir && dir[0] ? dir : "fsr4_shaders";
}

bool ReadFile(const std::string& path, std::vector<u8>& data) {
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    if (!file) {
        return false;
    }
    data.resize(size_t(file.tellg()));
    file.seekg(0);
    return bool(file.read(reinterpret_cast<char*>(data.data()), std::streamsize(data.size())));
}

constexpr vk::ImageSubresourceRange ColorRange{
    .aspectMask = vk::ImageAspectFlagBits::eColor,
    .levelCount = 1,
    .layerCount = 1,
};

} // namespace

bool Wanted() {
    static const bool wanted = [] {
        const char* v = std::getenv("ISSN_FSR4");
        return v && v[0] == '1';
    }();
    return wanted;
}

bool DeviceSupported() {
    return g_device_supported.load(std::memory_order_relaxed);
}

void SetDeviceSupported(bool supported) {
    g_device_supported.store(supported, std::memory_order_relaxed);
}

struct Upscaler::Impl {
    const Instance& instance;
    Scheduler& scheduler;
    std::vector<u8> scratch;
    FfxInterface backend{};
    bool backend_ok = false;
    ffxContext context{};
    bool context_ok = false;
    u32 out_width = 0, out_height = 0;
    u32 render_width = 0, render_height = 0;
    std::string problem;
    bool fatal = false;
    u64 next_frame_id = 1;
    std::deque<std::pair<u64, u64>> in_flight; ///< provider frame id, scheduler tick
    // FSR works on images of its own (tester 2026-10-21: the game's own images gave a yellow
    // picture and, through a depth+stencil view, a lost device): the colour and the motion
    // vectors are blitted in, the depth goes through a buffer into R32F, and the RGBA16F result
    // is blitted into the game's RGBA8 target.
    struct Owned {
        VideoCore::UniqueImage image;
        vk::UniqueImageView view;
        vk::ImageLayout layout = vk::ImageLayout::eUndefined;
        vk::Format format = vk::Format::eUndefined;
        u32 width = 0, height = 0;
    };
    Owned color_in, motion_in, depth_in, target;
    Owned color_raw, output_raw; ///< UNORM twins of sRGB images (UnormTwin), copied raw
    std::unique_ptr<VideoCore::UniqueBuffer> depth_buffer;
    u64 depth_buffer_size = 0;
    std::deque<std::pair<u64, Owned>> retired_images;
    std::deque<std::pair<u64, std::unique_ptr<VideoCore::UniqueBuffer>>> retired_buffers;
    // GPU time of the whole FSR 4 pass (copies, model, result), logged as an average.
    static constexpr u32 TimerSlots = 32;
    vk::UniqueQueryPool timer_pool;
    double timer_period_ns = 0.0, timer_sum_ms = 0.0, timer_max_ms = 0.0;
    u32 timer_count = 0;

    Impl(const Instance& instance_, Scheduler& scheduler_)
        : instance{instance_}, scheduler{scheduler_} {}

    ~Impl() {
        // The device is idle when the rasterizer goes (shutdown).
        if (context_ok) {
            ffxFsr4V07DestroyContext(&context, nullptr);
        }
        if (backend_ok) {
            ffxFsr4VkDestroyContext(reinterpret_cast<FfxFsr4VkContext*>(scratch.data()));
        }
    }

    void Fail(std::string reason, bool permanent) {
        if (problem != reason) {
            LOG_WARNING(Render_Vulkan, "FSR 4 unavailable: {} - the game's own anti-aliasing stays",
                        reason);
            std::printf("FSR 4: %s\n", reason.c_str());
            std::fflush(stdout);
        }
        problem = std::move(reason);
        fatal |= permanent;
    }

    /// The model trained for this scale (the presets' ratios: 1, 1.5, 1.7, 2, 3).
    static FfxFsr4ModelPreset PresetFor(u32 rw, u32 rh, u32 ow, u32 oh) {
        const double ratio = std::max(double(ow) / std::max(rw, 1u), double(oh) / std::max(rh, 1u));
        return ratio <= 1.05   ? FFX_FSR4_MODEL_PRESET_NATIVE_AA
               : ratio <= 1.6  ? FFX_FSR4_MODEL_PRESET_QUALITY
               : ratio <= 1.85 ? FFX_FSR4_MODEL_PRESET_BALANCED
               : ratio <= 2.5  ? FFX_FSR4_MODEL_PRESET_PERFORMANCE
                               : FFX_FSR4_MODEL_PRESET_ULTRA_PERFORMANCE;
    }

    static const char* PresetName(FfxFsr4ModelPreset preset) {
        switch (preset) {
        case FFX_FSR4_MODEL_PRESET_NATIVE_AA:
            return "Native AA";
        case FFX_FSR4_MODEL_PRESET_QUALITY:
            return "Quality";
        case FFX_FSR4_MODEL_PRESET_BALANCED:
            return "Balanced";
        case FFX_FSR4_MODEL_PRESET_PERFORMANCE:
            return "Performance";
        case FFX_FSR4_MODEL_PRESET_ULTRA_PERFORMANCE:
            return "Ultra Performance";
        default:
            return "?";
        }
    }

    bool CreateBackend(u32 rw, u32 rh, u32 ow, u32 oh) {
        const FfxFsr4ModelPreset preset = PresetFor(rw, rh, ow, oh);
        FfxFsr4V07AssetSet assets{};
        if (!ffxFsr4V07BuildAssetSet(preset, ow, oh, &assets)) {
            Fail("output size is not supported by the v07 model", true);
            return false;
        }
        const std::string dir = AssetDir() + "/";
        std::array<std::vector<u8>, FFX_FSR4_VK_PASS_COUNT> code;
        std::vector<u8> initializer, weights;
        const auto load = [&](const char* name, std::vector<u8>& data) {
            if (ReadFile(dir + "opt/" + name, data) || ReadFile(dir + name, data)) {
                return true;
            }
            Fail("missing " + dir + name + " (launcher: Graphics > Download FSR 4)", true);
            return false;
        };
        if (!load(assets.pre, code[0])) {
            return false;
        }
        for (u32 pass = 0; pass < FFX_FSR4_MODEL_PASS_COUNT; ++pass) {
            if (!load(assets.model[pass], code[1 + pass])) {
                return false;
            }
        }
        if (!load(assets.post, code[13]) || !load(assets.rcas, code[14]) ||
            !load(assets.spdAutoExposure, code[15]) || !load(assets.initializer, initializer) ||
            !load(assets.prePassWeights, weights)) {
            return false;
        }
        if (initializer.size() != FFX_FSR4_V07_INITIALIZER_BYTES ||
            weights.size() != FFX_FSR4_V07_PRE_PASS_WEIGHTS_BYTES) {
            Fail("model weights have an unexpected size", true);
            return false;
        }
        static const std::array<std::string, FFX_FSR4_VK_PASS_COUNT> entries = [] {
            std::array<std::string, FFX_FSR4_VK_PASS_COUNT> names;
            names.fill("main");
            for (u32 pass = 1; pass <= FFX_FSR4_MODEL_PASS_COUNT; ++pass) {
                names[pass] = "fsr4_model_v07_i8_pass" + std::to_string(pass);
            }
            return names;
        }();
        FfxFsr4VkCreateInfo ci{};
        ci.device = instance.GetDevice();
        ci.physicalDevice = instance.GetPhysicalDevice();
        for (u32 i = 0; i < FFX_FSR4_VK_PASS_COUNT; ++i) {
            if (code[i].size() % 4) {
                Fail("invalid SPIR-V asset", true);
                return false;
            }
            ci.shaders[i] = {reinterpret_cast<const uint32_t*>(code[i].data()), code[i].size(),
                             entries[i].c_str()};
        }
        ci.modelInitializer = initializer.data();
        ci.modelInitializerSize = initializer.size();
        ci.prePassWeights = weights.data();
        ci.prePassWeightsSize = weights.size();
        scratch.assign(ffxFsr4VkGetScratchMemorySize(), 0);
        ci.scratchBuffer = scratch.data();
        ci.scratchBufferSize = scratch.size();
        if (const VkResult result = ffxFsr4VkCreateContext(&ci, &backend); result != VK_SUCCESS) {
            Fail("Vulkan backend creation failed (" + std::to_string(int(result)) + ")", true);
            backend = {};
            return false;
        }
        backend_ok = true;
        if (!timer_pool) {
            const float period =
                instance.GetPhysicalDevice().getProperties().limits.timestampPeriod;
            auto [pool_result, pool] = instance.GetDevice().createQueryPoolUnique({
                .queryType = vk::QueryType::eTimestamp,
                .queryCount = TimerSlots * 2,
            });
            if (pool_result == vk::Result::eSuccess && period > 0.0f) {
                timer_pool = std::move(pool);
                timer_period_ns = period;
            }
        }

        ffxCreateContextDescUpscale desc{};
        desc.header.type = FFX_API_CREATE_CONTEXT_DESC_TYPE_UPSCALE;
        // The input is the game's tonemapped RGBA8 frame (no HDR, no exposure) and its depth is
        // reversed (GreaterEqual).
        desc.flags = FFX_UPSCALE_ENABLE_DEPTH_INVERTED;
        desc.maxRenderSize = {(rw + 7) & ~7u, (rh + 7) & ~7u};
        desc.maxUpscaleSize = {(ow + 7) & ~7u, (oh + 7) & ~7u};
        ffxFsr4V07SetBackendInterface(&backend);
        const auto created = ffxFsr4V07CreateContext(&context, &desc.header, nullptr);
        ffxFsr4V07SetBackendInterface(nullptr);
        if (created != FFX_API_RETURN_OK) {
            Fail("provider context creation failed (" + std::to_string(created) + ")", true);
            return false;
        }
        context_ok = true;
        out_width = ow;
        out_height = oh;
        render_width = rw;
        render_height = rh;
        LOG_WARNING(Render_Vulkan, "FSR 4: v07 INT8 {} running, {} tier, {}x{} -> {}x{}",
                    PresetName(preset), assets.tier, rw, rh, ow, oh);
        std::printf("FSR 4: v07 INT8 %s, %s tier, %ux%u -> %ux%u\n", PresetName(preset), assets.tier,
                    rw, rh, ow, oh);
        std::fflush(stdout);
        return true;
    }

    void ReadTimer(u64 frame_id) {
        if (!timer_pool) {
            return;
        }
        std::array<u64, 2> stamps{};
        const vk::Result result = instance.GetDevice().getQueryPoolResults(
            *timer_pool, u32(frame_id % TimerSlots) * 2, 2, sizeof(stamps), stamps.data(),
            sizeof(u64), vk::QueryResultFlagBits::e64);
        if (result != vk::Result::eSuccess || stamps[1] <= stamps[0]) {
            return;
        }
        const double ms = double(stamps[1] - stamps[0]) * timer_period_ns / 1e6;
        timer_sum_ms += ms;
        timer_max_ms = std::max(timer_max_ms, ms);
        if (++timer_count == 600) {
            LOG_WARNING(Render_Vulkan, "FSR 4: {:.2f} ms per frame on the GPU (max {:.2f}, 600 frames)",
                        timer_sum_ms / timer_count, timer_max_ms);
            std::printf("FSR 4: %.2f ms per frame on the GPU (max %.2f)\n", timer_sum_ms / timer_count,
                        timer_max_ms);
            std::fflush(stdout);
            timer_sum_ms = timer_max_ms = 0.0;
            timer_count = 0;
        }
    }

    void Retire() {
        while (!in_flight.empty() && scheduler.IsFree(in_flight.front().second)) {
            ffxFsr4VkRetireFrame(&backend, in_flight.front().first);
            ReadTimer(in_flight.front().first);
            in_flight.pop_front();
        }
        while (!retired_images.empty() && scheduler.IsFree(retired_images.front().first)) {
            retired_images.pop_front();
        }
        while (!retired_buffers.empty() && scheduler.IsFree(retired_buffers.front().first)) {
            retired_buffers.pop_front();
        }
    }

    /// `owned` as a width x height image of `format` (made again when either changes).
    void Ensure(Owned& owned, vk::Format format, u32 width, u32 height, bool transfer_only = false) {
        if (owned.image && owned.format == format && owned.width == width && owned.height == height) {
            return;
        }
        if (owned.image) {
            retired_images.emplace_back(scheduler.CurrentTick(), std::move(owned));
        }
        const vk::Device device = instance.GetDevice();
        owned.image = VideoCore::UniqueImage(device, instance.GetAllocator());
        owned.image.Create(vk::ImageCreateInfo{
            .imageType = vk::ImageType::e2D,
            .format = format,
            .extent{width, height, 1},
            .mipLevels = 1,
            .arrayLayers = 1,
            .samples = vk::SampleCountFlagBits::e1,
            .usage = (transfer_only ? vk::ImageUsageFlags{}
                                    : vk::ImageUsageFlagBits::eStorage |
                                          vk::ImageUsageFlagBits::eSampled) |
                     vk::ImageUsageFlagBits::eTransferSrc | vk::ImageUsageFlagBits::eTransferDst,
            .initialLayout = vk::ImageLayout::eUndefined,
        });
        if (transfer_only) {
            owned.view.reset();
            owned.layout = vk::ImageLayout::eUndefined;
            owned.format = format;
            owned.width = width;
            owned.height = height;
            return;
        }
        auto [result, view] = device.createImageViewUnique({
            .image = owned.image,
            .viewType = vk::ImageViewType::e2D,
            .format = format,
            .subresourceRange = ColorRange,
        });
        owned.view = std::move(view);
        owned.layout = vk::ImageLayout::eUndefined;
        owned.format = format;
        owned.width = width;
        owned.height = height;
    }

    static void To(vk::CommandBuffer cmdbuf, Owned& owned, vk::ImageLayout layout) {
        cmdbuf.pipelineBarrier(
            vk::PipelineStageFlagBits::eTransfer | vk::PipelineStageFlagBits::eComputeShader,
            vk::PipelineStageFlagBits::eTransfer | vk::PipelineStageFlagBits::eComputeShader, {},
            {}, {},
            vk::ImageMemoryBarrier{
                .srcAccessMask = vk::AccessFlagBits::eTransferWrite |
                                 vk::AccessFlagBits::eTransferRead |
                                 vk::AccessFlagBits::eShaderWrite | vk::AccessFlagBits::eShaderRead,
                .dstAccessMask = vk::AccessFlagBits::eTransferWrite |
                                 vk::AccessFlagBits::eTransferRead |
                                 vk::AccessFlagBits::eShaderWrite | vk::AccessFlagBits::eShaderRead,
                .oldLayout = owned.layout,
                .newLayout = layout,
                .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                .image = owned.image,
                .subresourceRange = ColorRange,
            });
        owned.layout = layout;
    }

    static void Blit(vk::CommandBuffer cmdbuf, vk::Image source, u32 sw, u32 sh, vk::Image dest,
                     u32 dw, u32 dh) {
        const vk::ImageSubresourceLayers layers{
            .aspectMask = vk::ImageAspectFlagBits::eColor,
            .mipLevel = 0,
            .baseArrayLayer = 0,
            .layerCount = 1,
        };
        const vk::ImageBlit blit{
            .srcSubresource = layers,
            .srcOffsets = std::array{vk::Offset3D{0, 0, 0}, vk::Offset3D{s32(sw), s32(sh), 1}},
            .dstSubresource = layers,
            .dstOffsets = std::array{vk::Offset3D{0, 0, 0}, vk::Offset3D{s32(dw), s32(dh), 1}},
        };
        cmdbuf.blitImage(source, vk::ImageLayout::eTransferSrcOptimal, dest,
                         vk::ImageLayout::eTransferDstOptimal, blit, vk::Filter::eNearest);
    }

    static void Copy(vk::CommandBuffer cmdbuf, vk::Image source, vk::Image dest, u32 w, u32 h) {
        const vk::ImageSubresourceLayers layers{
            .aspectMask = vk::ImageAspectFlagBits::eColor,
            .mipLevel = 0,
            .baseArrayLayer = 0,
            .layerCount = 1,
        };
        cmdbuf.copyImage(source, vk::ImageLayout::eTransferSrcOptimal, dest,
                         vk::ImageLayout::eTransferDstOptimal,
                         vk::ImageCopy{.srcSubresource = layers,
                                       .dstSubresource = layers,
                                       .extent{w, h, 1}});
    }

    /// The game's images (layout TransferSrcOptimal, set by the caller) into FSR's own.
    void CopyInputs(vk::CommandBuffer cmdbuf, const Input& color, const Input& motion,
                    const Input& depth) {
        Ensure(color_in, vk::Format::eR16G16B16A16Sfloat, color.width, color.height);
        Ensure(motion_in, vk::Format::eR16G16Sfloat, motion.width, motion.height);
        Ensure(depth_in, vk::Format::eR32Sfloat, depth.width, depth.height);
        const u64 depth_bytes = u64(depth.width) * depth.height * 4;
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
        for (Owned* owned : {&color_in, &motion_in, &depth_in}) {
            To(cmdbuf, *owned, vk::ImageLayout::eTransferDstOptimal);
        }
        if (const vk::Format twin = UnormTwin(color.format); twin != color.format) {
            Ensure(color_raw, twin, color.width, color.height, true);
            To(cmdbuf, color_raw, vk::ImageLayout::eTransferDstOptimal);
            Copy(cmdbuf, color.image, color_raw.image, color.width, color.height);
            To(cmdbuf, color_raw, vk::ImageLayout::eTransferSrcOptimal);
            Blit(cmdbuf, color_raw.image, color.width, color.height, color_in.image, color.width,
                 color.height);
        } else {
            Blit(cmdbuf, color.image, color.width, color.height, color_in.image, color.width,
                 color.height);
        }
        Blit(cmdbuf, motion.image, motion.width, motion.height, motion_in.image, motion.width,
             motion.height);
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
            .imageExtent{depth.width, depth.height, 1},
        };
        cmdbuf.copyImageToBuffer(depth.image, vk::ImageLayout::eTransferSrcOptimal, *depth_buffer,
                                 depth_copy);
        cmdbuf.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer,
                               vk::PipelineStageFlagBits::eTransfer, {},
                               vk::MemoryBarrier{
                                   .srcAccessMask = vk::AccessFlagBits::eTransferWrite,
                                   .dstAccessMask = vk::AccessFlagBits::eTransferRead,
                               },
                               {}, {});
        vk::BufferImageCopy color_copy = depth_copy;
        color_copy.imageSubresource.aspectMask = vk::ImageAspectFlagBits::eColor;
        cmdbuf.copyBufferToImage(*depth_buffer, depth_in.image, vk::ImageLayout::eTransferDstOptimal,
                                 color_copy);
        for (Owned* owned : {&color_in, &motion_in, &depth_in}) {
            To(cmdbuf, *owned, vk::ImageLayout::eGeneral);
        }
    }

    VkResult Register(vk::Image image, vk::ImageView view, VkAccessFlags access) {
        const FfxFsr4VkExternalImageState state{
            .structSize = sizeof(FfxFsr4VkExternalImageState),
            .image = image,
            .view = view,
            .layout = VK_IMAGE_LAYOUT_GENERAL,
            .stageMask = VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
            .accessMask = access,
            .restoreLayout = VK_IMAGE_LAYOUT_GENERAL,
            .restoreStageMask = VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
            .restoreAccessMask = access,
        };
        return ffxFsr4VkSetExternalImageState(&backend, &state);
    }

    static void Layout(vk::CommandBuffer cmdbuf, vk::Image image, vk::ImageLayout from,
                       vk::ImageLayout to) {
        if (from == to) {
            return;
        }
        cmdbuf.pipelineBarrier(
            vk::PipelineStageFlagBits::eAllCommands, vk::PipelineStageFlagBits::eAllCommands, {},
            {}, {},
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

    bool Record(vk::CommandBuffer cmdbuf, const Input& color, const Input& motion,
                const Input& depth, const Output& out, float jx, float jy, bool reset) {
        const vk::Image output = out.image;
        const vk::Format output_format = out.format;
        const u32 ow = out.width, oh = out.height;
        if (fatal) {
            return false;
        }
        if (!DeviceSupported()) {
            Fail("the GPU lacks INT8 dot products / compute derivatives (FSR 4 needs a Radeon "
                 "RX 7000 / 9000 or similar)",
                 true);
            return false;
        }
        if (!context_ok || ow != out_width || oh != out_height || color.width != render_width ||
            color.height != render_height) {
            if (context_ok || backend_ok) {
                // A size change (rare: the game renders 1920x1080) - the old context's frames
                // must be done first. issn: the whole device, not only this scheduler (the
                // presenter's queue may still read FSR's images; AMD driver fault, 10-27).
                scheduler.Finish();
                (void)instance.GetDevice().waitIdle();
                while (!in_flight.empty()) {
                    ffxFsr4VkRetireFrame(&backend, in_flight.front().first);
                    in_flight.pop_front();
                }
                if (context_ok) {
                    ffxFsr4V07DestroyContext(&context, nullptr);
                    context_ok = false;
                }
                if (backend_ok) {
                    ffxFsr4VkDestroyContext(reinterpret_cast<FfxFsr4VkContext*>(scratch.data()));
                    backend_ok = false;
                }
                in_flight.clear();
            }
            if (!CreateBackend(color.width, color.height, ow, oh)) {
                return false;
            }
            reset = true;
        }
        Retire();
        const u64 frame_id = next_frame_id++;
        VkResult begin = ffxFsr4VkBeginFrame(&backend, frame_id);
        while (begin == VK_NOT_READY && !in_flight.empty()) {
            scheduler.Wait(in_flight.front().second);
            Retire();
            begin = ffxFsr4VkBeginFrame(&backend, frame_id);
        }
        if (begin != VK_SUCCESS) {
            Fail("no free provider frame (" + std::to_string(int(begin)) + ")", false);
            return false;
        }
        // A frame begun must reach RetireFrame even when it records nothing.
        const auto abandon = [&] { in_flight.emplace_back(frame_id, scheduler.CurrentTick()); };

        const u32 timer_slot = u32(frame_id % TimerSlots) * 2;
        if (timer_pool) {
            cmdbuf.resetQueryPool(*timer_pool, timer_slot, 2);
            // All-commands: the start waits for the game's earlier work, so only FSR is timed.
            cmdbuf.writeTimestamp(vk::PipelineStageFlagBits::eAllCommands, *timer_pool, timer_slot);
        }
        CopyInputs(cmdbuf, color, motion, depth);
        Ensure(target, vk::Format::eR16G16B16A16Sfloat, ow, oh);
        To(cmdbuf, target, vk::ImageLayout::eGeneral);

        constexpr VkAccessFlags read = VK_ACCESS_SHADER_READ_BIT;
        const std::array<std::tuple<Owned*, VkAccessFlags, const char*>, 4> images{{
            {&color_in, read, "color"},
            {&depth_in, read, "depth"},
            {&motion_in, read, "motion"},
            {&target, read | VK_ACCESS_SHADER_WRITE_BIT, "output"},
        }};
        for (const auto& [owned, access, name] : images) {
            if (const VkResult result = Register(owned->image, *owned->view, access);
                result != VK_SUCCESS) {
                Fail(std::string{"external image registration failed ("} + name + ", " +
                         std::to_string(int(result)) + ")",
                     false);
                abandon();
                return false;
            }
        }
        const auto resource = [](const Owned& owned, u32 format, u32 state) {
            FfxApiResource r{};
            r.resource = reinterpret_cast<void*>(VkImageView(*owned.view));
            r.description.type = FFX_RESOURCE_TYPE_TEXTURE2D;
            r.description.format = format;
            r.description.width = owned.width;
            r.description.height = owned.height;
            r.description.depth = 1;
            r.description.mipCount = 1;
            r.state = state;
            return r;
        };
        ffxDispatchDescUpscale d{};
        d.header.type = FFX_API_DISPATCH_DESC_TYPE_UPSCALE;
        d.commandList = reinterpret_cast<void*>(VkCommandBuffer(cmdbuf));
        d.color = resource(color_in, FFX_SURFACE_FORMAT_R16G16B16A16_FLOAT,
                           FFX_API_RESOURCE_STATE_COMPUTE_READ);
        d.depth = resource(depth_in, FFX_SURFACE_FORMAT_R32_FLOAT, FFX_API_RESOURCE_STATE_COMPUTE_READ);
        d.motionVectors = resource(motion_in, FFX_SURFACE_FORMAT_R16G16_FLOAT,
                                   FFX_API_RESOURCE_STATE_COMPUTE_READ);
        d.output = resource(target, FFX_SURFACE_FORMAT_R16G16B16A16_FLOAT,
                            FFX_API_RESOURCE_STATE_UNORDERED_ACCESS);
        d.jitterOffset = {jx, jy};
        // The game's vectors are screen UV, current minus previous; FSR wants render pixels
        // towards the previous position. ISSN_FSR4_MV=+ flips the sign (calibration).
        static const float mv_sign = [] {
            const char* v = std::getenv("ISSN_FSR4_MV");
            return v && v[0] == '+' ? 1.0f : -1.0f;
        }();
        d.motionVectorScale = {mv_sign * float(motion.width), mv_sign * float(motion.height)};
        d.renderSize = {color.width, color.height};
        d.upscaleSize = {ow, oh};
        // ISSN_FSR4_SHARPNESS (0..1, default 0.2): RCAS after the model.
        static const float sharpness = [] {
            const char* v = std::getenv("ISSN_FSR4_SHARPNESS");
            const float s = v && *v ? float(std::atof(v)) : 0.2f;
            return s < 0.0f ? 0.0f : (s > 1.0f ? 1.0f : s);
        }();
        d.enableSharpening = sharpness > 0.0f;
        d.sharpness = sharpness;
        d.enableAutoExposure = false;
        d.frameTimeDelta = 16.6f;
        d.preExposure = 1.0f;
        d.reset = reset;
        d.cameraNear = 0.1f;
        d.cameraFar = 10000.0f;
        d.cameraFovAngleVertical = 1.0f;
        d.viewSpaceToMetersFactor = 1.0f;
        if (const auto result = ffxFsr4V07Dispatch(&context, &d.header);
            result != FFX_API_RETURN_OK) {
            Fail("dispatch failed (" + std::to_string(result) + ")", false);
            abandon();
            return false;
        }
        in_flight.emplace_back(frame_id, scheduler.CurrentTick());

        // FSR's RGBA16F result into the output (the resolve's target or the upscaler's scene).
        To(cmdbuf, target, vk::ImageLayout::eTransferSrcOptimal);
        Layout(cmdbuf, output, out.layout, vk::ImageLayout::eTransferDstOptimal);
        if (const vk::Format twin = UnormTwin(output_format); twin != output_format) {
            Ensure(output_raw, twin, ow, oh, true);
            To(cmdbuf, output_raw, vk::ImageLayout::eTransferDstOptimal);
            Blit(cmdbuf, target.image, ow, oh, output_raw.image, ow, oh);
            To(cmdbuf, output_raw, vk::ImageLayout::eTransferSrcOptimal);
            Copy(cmdbuf, output_raw.image, output, ow, oh);
        } else {
            Blit(cmdbuf, target.image, ow, oh, output, ow, oh);
        }
        Layout(cmdbuf, output, vk::ImageLayout::eTransferDstOptimal, out.layout);
        if (timer_pool) {
            cmdbuf.writeTimestamp(vk::PipelineStageFlagBits::eAllCommands, *timer_pool,
                                  timer_slot + 1);
        }
        problem.clear();
        return true;
    }
};

Upscaler::Upscaler(const Instance& instance, Scheduler& scheduler)
    : impl{std::make_unique<Impl>(instance, scheduler)} {}

Upscaler::~Upscaler() = default;

bool Upscaler::Record(vk::CommandBuffer cmdbuf, const Input& color, const Input& motion,
                      const Input& depth, const Output& output, float jitter_x, float jitter_y,
                      bool reset) {
    return impl->Record(cmdbuf, color, motion, depth, output, jitter_x, jitter_y, reset);
}

const std::string& Upscaler::Problem() const noexcept {
    return impl->problem;
}

} // namespace Vulkan::Fsr4Aa
