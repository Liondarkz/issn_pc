// SPDX-License-Identifier: GPL-2.0-or-later
// issn-axy: Intel XeSS Super Resolution in the slot DLSS and FSR 4 use: the game's SMAA T2x
// temporal resolve is replaced by XeSS from the same inputs (tonemap output, motion vectors,
// reversed-Z depth, the game's +-0.25 px jitter). ISSN_XESS=1 turns it on (vk_dlss.h Mode() == 4).
// XeSS-SR runs on Vulkan on any vendor's GPU that has shaderStorageImageWriteWithoutFormat and
// VK_EXT_mutable_descriptor_type (Radeon RX 5000 and later, GeForce GTX 10 and later, Arc).
//
// Only Intel's MIT headers are compiled in (third_party/xess-sdk). The runtime, libxess.dll, is
// loaded at run time from the xess folder next to the EXE (ISSN_XESS_DIR overrides): the player
// copies it there from Intel's SDK, as with DLSS. Missing DLL, a GPU without the features or any
// failure: the game's own anti-aliasing stays.

#pragma once

#include <memory>
#include <string>
#include <vector>
#include "common/types.h"
#include "video_core/renderer_vulkan/issn_fsr4.h"
#include "video_core/renderer_vulkan/vk_common.h"

namespace Vulkan {

class Instance;
class Scheduler;

namespace Xess {

/// ISSN_XESS=1.
bool Wanted();

/// Before vkCreateInstance: loads libxess.dll and adds the instance extensions XeSS needs.
void AddInstanceExtensions(std::vector<const char*>& extensions);

/// Before vkCreateDevice: the device extensions XeSS needs on this GPU into `out`. False when it
/// cannot run here (no DLL, or XeSS refused the device).
bool DeviceExtensions(vk::Instance instance, vk::PhysicalDevice physical_device,
                      std::vector<const char*>& out);

/// The feature structures of the device being created, which XeSS's requirements are merged into:
/// promoted features go into the Vulkan 1.1 / 1.2 / 1.3 structures, the rest (the mutable
/// descriptor type feature) is spliced into the create info's pNext chain.
struct FeatureTargets {
    vk::DeviceCreateInfo* create_info;
    vk::PhysicalDeviceFeatures* core;
    vk::PhysicalDeviceVulkan11Features* v11;
    vk::PhysicalDeviceVulkan12Features* v12;
    vk::PhysicalDeviceVulkan13Features* v13;
};
/// False when XeSS's feature query failed (the device then lacks what it needs).
bool AddDeviceFeatures(vk::Instance instance, vk::PhysicalDevice physical_device,
                       const FeatureTargets& targets);

/// The device was created with XeSS's extensions and features (set by vk_instance).
bool DeviceSupported();
void SetDeviceSupported(bool supported);

class Upscaler {
public:
    Upscaler(const Instance& instance, Scheduler& scheduler);
    ~Upscaler();

    /// Records XeSS into `cmdbuf`, as Fsr4Aa::Upscaler::Record: color / motion / depth (layout
    /// TransferSrc) at the render size, `output` at the same size (XeSS's native AA) or larger
    /// (upscaling; the quality mode follows the ratio). False = not run; the caller falls back.
    bool Record(vk::CommandBuffer cmdbuf, const Fsr4Aa::Input& color, const Fsr4Aa::Input& motion,
                const Fsr4Aa::Input& depth, const Fsr4Aa::Output& output, float jitter_x,
                float jitter_y, bool reset);

    /// The last failure, or empty; permanent ones stop further attempts.
    [[nodiscard]] const std::string& Problem() const noexcept;

private:
    struct Impl;
    std::unique_ptr<Impl> impl;
};

} // namespace Xess
} // namespace Vulkan
