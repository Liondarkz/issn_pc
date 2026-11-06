// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cstdio>
#include "common/logging/log.h"
#include "video_core/host_shaders/issn_sharpen_comp.h"
#include "video_core/renderer_vulkan/issn_sharpen.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_platform.h"
#include "video_core/renderer_vulkan/vk_shader_util.h"

#ifdef _WIN32
#include <filesystem>
#include <windows.h>
#endif

namespace Vulkan::Sharpen {

bool OptiScalerPresent() {
#ifdef _WIN32
    static const bool present = [] {
        wchar_t path[MAX_PATH];
        const DWORD n = GetModuleFileNameW(nullptr, path, MAX_PATH);
        if (n == 0 || n >= MAX_PATH) {
            return false;
        }
        std::error_code ec;
        const std::filesystem::path dir = std::filesystem::path(path).parent_path();
        return std::filesystem::is_regular_file(dir / L"winmm.dll", ec) &&
               std::filesystem::is_regular_file(dir / L"OptiScaler.ini", ec);
    }();
    return present;
#else
    return false;
#endif
}

namespace {
constexpr vk::ImageSubresourceRange ColorRange{
    .aspectMask = vk::ImageAspectFlagBits::eColor,
    .baseMipLevel = 0,
    .levelCount = 1,
    .baseArrayLayer = 0,
    .layerCount = 1,
};
constexpr vk::ImageSubresourceLayers ColorLayers{
    .aspectMask = vk::ImageAspectFlagBits::eColor,
    .mipLevel = 0,
    .baseArrayLayer = 0,
    .layerCount = 1,
};
constexpr vk::Format ResultFormat = vk::Format::eR16G16B16A16Sfloat;
} // namespace

struct Pass::Images {
    VideoCore::UniqueImage copy;   ///< the input, in its own format (sampled)
    VideoCore::UniqueImage result; ///< RCAS output, RGBA16F (storage, blit source)
    vk::UniqueImageView copy_view;
    vk::UniqueImageView result_view;
    vk::Format format{};
    u32 width{}, height{};
};

Pass::Pass(const Instance& instance_) : instance{instance_}, device{instance_.GetDevice()} {
    sampler = Check<"sharpen sampler">(device.createSamplerUnique(vk::SamplerCreateInfo{
        .magFilter = vk::Filter::eNearest,
        .minFilter = vk::Filter::eNearest,
        .mipmapMode = vk::SamplerMipmapMode::eNearest,
        .addressModeU = vk::SamplerAddressMode::eClampToEdge,
        .addressModeV = vk::SamplerAddressMode::eClampToEdge,
        .addressModeW = vk::SamplerAddressMode::eClampToEdge,
        .maxAnisotropy = 1.0f,
        .minLod = 0.0f,
        .maxLod = 0.0f,
    }));
    const std::array<vk::DescriptorSetLayoutBinding, 3> bindings{{
        {.binding = 0,
         .descriptorType = vk::DescriptorType::eSampledImage,
         .descriptorCount = 1,
         .stageFlags = vk::ShaderStageFlagBits::eCompute},
        {.binding = 1,
         .descriptorType = vk::DescriptorType::eStorageImage,
         .descriptorCount = 1,
         .stageFlags = vk::ShaderStageFlagBits::eCompute},
        {.binding = 2,
         .descriptorType = vk::DescriptorType::eSampler,
         .descriptorCount = 1,
         .stageFlags = vk::ShaderStageFlagBits::eCompute,
         .pImmutableSamplers = &sampler.get()},
    }};
    set_layout = Check<"sharpen set layout">(device.createDescriptorSetLayoutUnique({
        .flags = vk::DescriptorSetLayoutCreateFlagBits::ePushDescriptor,
        .bindingCount = static_cast<u32>(bindings.size()),
        .pBindings = bindings.data(),
    }));
    const vk::PushConstantRange push{
        .stageFlags = vk::ShaderStageFlagBits::eCompute,
        .offset = 0,
        .size = sizeof(u32) * 4,
    };
    pipeline_layout = Check<"sharpen pipeline layout">(device.createPipelineLayoutUnique({
        .setLayoutCount = 1,
        .pSetLayouts = &set_layout.get(),
        .pushConstantRangeCount = 1,
        .pPushConstantRanges = &push,
    }));
    const vk::ShaderModule module =
        Compile(HostShaders::ISSN_SHARPEN_COMP, vk::ShaderStageFlagBits::eCompute, device, {});
    if (!module) {
        LOG_ERROR(Render_Vulkan, "Sharpening: the RCAS shader did not compile; sharpening is off");
        return;
    }
    SetObjectName(device, module, "issn_sharpen.comp");
    pipeline = Check<"sharpen pipeline">(device.createComputePipelineUnique(
        {}, vk::ComputePipelineCreateInfo{
                .stage{.stage = vk::ShaderStageFlagBits::eCompute, .module = module, .pName = "main"},
                .layout = pipeline_layout.get(),
            }));
    device.destroyShaderModule(module);
}

Pass::~Pass() = default;

bool Pass::Run(vk::CommandBuffer cmdbuf, vk::Image image, vk::Format format, vk::ImageLayout layout,
               u32 width, u32 height, int percent) {
    if (!pipeline || ClampStrength(percent) == 0 || !image || width == 0 || height == 0) {
        return false;
    }
    if (format != blit_checked) {
        // The result goes back by a blit (format conversion, sRGB included): the target's
        // format must take it, and be sampled for the copy.
        blit_checked = format;
        const vk::FormatFeatureFlags features =
            instance.GetPhysicalDevice().getFormatProperties(format).optimalTilingFeatures;
        blit_ok = (features & vk::FormatFeatureFlagBits::eBlitDst) &&
                  (features & vk::FormatFeatureFlagBits::eSampledImage);
        if (!blit_ok) {
            LOG_WARNING(Render_Vulkan, "Sharpening: {} cannot be blitted to here; no sharpening",
                        vk::to_string(format));
        }
    }
    if (!blit_ok) {
        return false;
    }
    ++runs;
    while (!retired.empty() && runs - retired.front().first > 16) {
        retired.pop_front();
    }
    if (!images || images->format != format || images->width != width ||
        images->height != height) {
        // A new size or format (a resolution change): frames in flight may still use the old
        // images, so they are freed some runs later.
        if (images) {
            retired.emplace_back(runs, std::move(images));
        }
        auto made = std::make_unique<Images>();
        made->copy = VideoCore::UniqueImage(device, instance.GetAllocator());
        made->result = VideoCore::UniqueImage(device, instance.GetAllocator());
        vk::ImageCreateInfo info{
            .imageType = vk::ImageType::e2D,
            .format = format,
            .extent = {width, height, 1},
            .mipLevels = 1,
            .arrayLayers = 1,
            .samples = vk::SampleCountFlagBits::e1,
            .tiling = vk::ImageTiling::eOptimal,
            .usage = vk::ImageUsageFlagBits::eTransferDst | vk::ImageUsageFlagBits::eSampled,
            .initialLayout = vk::ImageLayout::eUndefined,
        };
        made->copy.Create(info);
        info.format = ResultFormat;
        info.usage = vk::ImageUsageFlagBits::eStorage | vk::ImageUsageFlagBits::eTransferSrc;
        made->result.Create(info);
        SetObjectName(device, static_cast<vk::Image>(made->copy), "Sharpen input copy");
        SetObjectName(device, static_cast<vk::Image>(made->result), "Sharpen result");
        made->copy_view = Check<"sharpen copy view">(device.createImageViewUnique({
            .image = made->copy,
            .viewType = vk::ImageViewType::e2D,
            .format = format,
            .subresourceRange = ColorRange,
        }));
        made->result_view = Check<"sharpen result view">(device.createImageViewUnique({
            .image = made->result,
            .viewType = vk::ImageViewType::e2D,
            .format = ResultFormat,
            .subresourceRange = ColorRange,
        }));
        made->format = format;
        made->width = width;
        made->height = height;
        images = std::move(made);
        LOG_INFO(Render_Vulkan, "Sharpening: RCAS pass at {}x{} ({})", width, height,
                 vk::to_string(format));
    }
    const vk::Image copy = images->copy;
    const vk::Image result = images->result;

    // 1. The target to a copy source (after whatever wrote it); the copy image to a transfer
    //    destination (after the previous frame's read of it).
    const std::array to_copy{
        vk::ImageMemoryBarrier2{
            .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .srcAccessMask = vk::AccessFlagBits2::eMemoryWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eCopy,
            .dstAccessMask = vk::AccessFlagBits2::eTransferRead,
            .oldLayout = layout,
            .newLayout = vk::ImageLayout::eTransferSrcOptimal,
            .image = image,
            .subresourceRange = ColorRange,
        },
        vk::ImageMemoryBarrier2{
            .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .dstStageMask = vk::PipelineStageFlagBits2::eCopy,
            .dstAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .oldLayout = vk::ImageLayout::eUndefined,
            .newLayout = vk::ImageLayout::eTransferDstOptimal,
            .image = copy,
            .subresourceRange = ColorRange,
        },
    };
    cmdbuf.pipelineBarrier2({.imageMemoryBarrierCount = static_cast<u32>(to_copy.size()),
                             .pImageMemoryBarriers = to_copy.data()});
    cmdbuf.copyImage(image, vk::ImageLayout::eTransferSrcOptimal, copy,
                     vk::ImageLayout::eTransferDstOptimal,
                     vk::ImageCopy{.srcSubresource = ColorLayers,
                                   .dstSubresource = ColorLayers,
                                   .extent = {width, height, 1}});

    // 2. RCAS: copy -> result.
    const std::array to_filter{
        vk::ImageMemoryBarrier2{
            .srcStageMask = vk::PipelineStageFlagBits2::eCopy,
            .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eComputeShader,
            .dstAccessMask = vk::AccessFlagBits2::eShaderSampledRead,
            .oldLayout = vk::ImageLayout::eTransferDstOptimal,
            .newLayout = vk::ImageLayout::eShaderReadOnlyOptimal,
            .image = copy,
            .subresourceRange = ColorRange,
        },
        vk::ImageMemoryBarrier2{
            .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .dstStageMask = vk::PipelineStageFlagBits2::eComputeShader,
            .dstAccessMask = vk::AccessFlagBits2::eShaderStorageWrite,
            .oldLayout = vk::ImageLayout::eUndefined,
            .newLayout = vk::ImageLayout::eGeneral,
            .image = result,
            .subresourceRange = ColorRange,
        },
    };
    cmdbuf.pipelineBarrier2({.imageMemoryBarrierCount = static_cast<u32>(to_filter.size()),
                             .pImageMemoryBarriers = to_filter.data()});
    const vk::DescriptorImageInfo input_info{.imageView = images->copy_view.get(),
                                             .imageLayout = vk::ImageLayout::eShaderReadOnlyOptimal};
    const vk::DescriptorImageInfo output_info{.imageView = images->result_view.get(),
                                              .imageLayout = vk::ImageLayout::eGeneral};
    const vk::DescriptorImageInfo sampler_info{.sampler = sampler.get()};
    const std::array writes{
        vk::WriteDescriptorSet{.dstBinding = 0,
                               .descriptorCount = 1,
                               .descriptorType = vk::DescriptorType::eSampledImage,
                               .pImageInfo = &input_info},
        vk::WriteDescriptorSet{.dstBinding = 1,
                               .descriptorCount = 1,
                               .descriptorType = vk::DescriptorType::eStorageImage,
                               .pImageInfo = &output_info},
        vk::WriteDescriptorSet{.dstBinding = 2,
                               .descriptorCount = 1,
                               .descriptorType = vk::DescriptorType::eSampler,
                               .pImageInfo = &sampler_info},
    };
    const std::array<u32, 4> constants = RcasConstants(Attenuation(percent));
    cmdbuf.bindPipeline(vk::PipelineBindPoint::eCompute, pipeline.get());
    cmdbuf.pushDescriptorSetKHR(vk::PipelineBindPoint::eCompute, pipeline_layout.get(), 0, writes);
    cmdbuf.pushConstants(pipeline_layout.get(), vk::ShaderStageFlagBits::eCompute, 0,
                         sizeof(constants), constants.data());
    cmdbuf.dispatch((width + 7) / 8, (height + 7) / 8, 1);

    // 3. Back into the target by a blit (the format conversion), then the target's own layout.
    const std::array to_blit{
        vk::ImageMemoryBarrier2{
            .srcStageMask = vk::PipelineStageFlagBits2::eComputeShader,
            .srcAccessMask = vk::AccessFlagBits2::eShaderStorageWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eBlit,
            .dstAccessMask = vk::AccessFlagBits2::eTransferRead,
            .oldLayout = vk::ImageLayout::eGeneral,
            .newLayout = vk::ImageLayout::eTransferSrcOptimal,
            .image = result,
            .subresourceRange = ColorRange,
        },
        vk::ImageMemoryBarrier2{
            .srcStageMask = vk::PipelineStageFlagBits2::eCopy,
            .srcAccessMask = vk::AccessFlagBits2::eTransferRead,
            .dstStageMask = vk::PipelineStageFlagBits2::eBlit,
            .dstAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .oldLayout = vk::ImageLayout::eTransferSrcOptimal,
            .newLayout = vk::ImageLayout::eTransferDstOptimal,
            .image = image,
            .subresourceRange = ColorRange,
        },
    };
    cmdbuf.pipelineBarrier2({.imageMemoryBarrierCount = static_cast<u32>(to_blit.size()),
                             .pImageMemoryBarriers = to_blit.data()});
    const std::array<vk::Offset3D, 2> corners{vk::Offset3D{0, 0, 0},
                                              vk::Offset3D{s32(width), s32(height), 1}};
    cmdbuf.blitImage(result, vk::ImageLayout::eTransferSrcOptimal, image,
                     vk::ImageLayout::eTransferDstOptimal,
                     vk::ImageBlit{.srcSubresource = ColorLayers,
                                   .srcOffsets = corners,
                                   .dstSubresource = ColorLayers,
                                   .dstOffsets = corners},
                     vk::Filter::eNearest);
    const vk::ImageMemoryBarrier2 back{
        .srcStageMask = vk::PipelineStageFlagBits2::eBlit,
        .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
        .dstStageMask = vk::PipelineStageFlagBits2::eAllCommands,
        .dstAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
        .oldLayout = vk::ImageLayout::eTransferDstOptimal,
        .newLayout = layout,
        .image = image,
        .subresourceRange = ColorRange,
    };
    cmdbuf.pipelineBarrier2({.imageMemoryBarrierCount = 1, .pImageMemoryBarriers = &back});
    return true;
}

} // namespace Vulkan::Sharpen
