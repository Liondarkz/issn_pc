// SPDX-License-Identifier: GPL-2.0-or-later
// issn-axy: XeSS Super Resolution in the T2x resolve's place (issn_xess.h). The image handling
// follows issn_fsr4.cpp: the game's images are copied into images of XeSS's own (RGBA16F colour,
// RG16F motion, R32F depth) and the RGBA16F result is blitted into the target.
#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <dlfcn.h>
#include <unistd.h>
#endif

#include "video_core/renderer_vulkan/issn_xess.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <filesystem>
#include <utility>

#include "common/logging/log.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/texture_cache/image.h"
#include "xess/xess_vk.h"

namespace Vulkan::Xess {

namespace {

std::atomic<bool> g_device_supported{false};

// libxess.dll's entry points, resolved once (Load).
struct Api {
#ifdef _WIN32
    HMODULE module = nullptr;
#else
    void* module = nullptr;
#endif
    decltype(&xessGetVersion) get_version = nullptr;
    decltype(&xessVKGetRequiredInstanceExtensions) instance_extensions = nullptr;
    decltype(&xessVKGetRequiredDeviceExtensions) device_extensions = nullptr;
    decltype(&xessVKGetRequiredDeviceFeatures) device_features = nullptr;
    decltype(&xessVKCreateContext) create_context = nullptr;
    decltype(&xessVKBuildPipelines) build_pipelines = nullptr;
    decltype(&xessVKInit) init = nullptr;
    decltype(&xessVKExecute) execute = nullptr;
    decltype(&xessDestroyContext) destroy_context = nullptr;
    decltype(&xessGetOptimalInputResolution) optimal_input = nullptr;
    decltype(&xessSetVelocityScale) set_velocity_scale = nullptr;
    decltype(&xessSetLoggingCallback) set_logging = nullptr;
    bool loaded = false;
    std::vector<const char*> device_exts; ///< what XeSS asked for this GPU (its own strings)
    bool device_query_ok = false;
};
Api g_api;

std::filesystem::path LibDir() {
    if (const char* dir = std::getenv("ISSN_XESS_DIR"); dir && dir[0]) {
        return dir;
    }
#ifdef _WIN32
    std::wstring buf(MAX_PATH, L'\0');
    for (;;) {
        const DWORD n = GetModuleFileNameW(nullptr, buf.data(), static_cast<DWORD>(buf.size()));
        if (n == 0) {
            return "xess";
        }
        if (n < buf.size()) {
            buf.resize(n);
            break;
        }
        buf.resize(buf.size() * 2);
    }
    return std::filesystem::path{buf}.parent_path() / "xess";
#else
    std::error_code ec;
    const auto exe = std::filesystem::read_symlink("/proc/self/exe", ec);
    return ec ? std::filesystem::path{"xess"} : exe.parent_path() / "xess";
#endif
}

template <typename T>
bool Resolve(T& out, const char* name) {
#ifdef _WIN32
    out = reinterpret_cast<T>(GetProcAddress(g_api.module, name));
#else
    out = reinterpret_cast<T>(dlsym(g_api.module, name));
#endif
    if (!out) {
        LOG_WARNING(Render_Vulkan, "XeSS: the library has no {}", name);
    }
    return out != nullptr;
}

const char* ResultName(xess_result_t r) {
    switch (r) {
    case XESS_RESULT_SUCCESS:
        return "success";
    case XESS_RESULT_WARNING_NONEXISTING_FOLDER:
        return "warning: folder missing";
    case XESS_RESULT_WARNING_OLD_DRIVER:
        return "warning: old driver";
    case XESS_RESULT_ERROR_UNSUPPORTED_DEVICE:
        return "unsupported device";
    case XESS_RESULT_ERROR_UNSUPPORTED_DRIVER:
        return "unsupported driver";
    case XESS_RESULT_ERROR_UNINITIALIZED:
        return "uninitialized";
    case XESS_RESULT_ERROR_INVALID_ARGUMENT:
        return "invalid argument";
    case XESS_RESULT_ERROR_DEVICE_OUT_OF_MEMORY:
        return "out of video memory";
    case XESS_RESULT_ERROR_DEVICE:
        return "device error";
    case XESS_RESULT_ERROR_NOT_IMPLEMENTED:
        return "not implemented";
    case XESS_RESULT_ERROR_INVALID_CONTEXT:
        return "invalid context";
    case XESS_RESULT_ERROR_OPERATION_IN_PROGRESS:
        return "operation in progress";
    case XESS_RESULT_ERROR_UNSUPPORTED:
        return "unsupported";
    case XESS_RESULT_ERROR_CANT_LOAD_LIBRARY:
        return "cannot load a library";
    case XESS_RESULT_ERROR_WRONG_CALL_ORDER:
        return "wrong call order";
    default:
        return "unknown error";
    }
}

std::string Describe(xess_result_t r) {
    return std::string{ResultName(r)} + " (" + std::to_string(int(r)) + ")";
}

void LogMessage(const char* message, xess_logging_level_t level) {
    std::string text{message ? message : ""};
    while (!text.empty() && (text.back() == '\n' || text.back() == '\r')) {
        text.pop_back();
    }
    if (level >= XESS_LOGGING_LEVEL_ERROR) {
        LOG_ERROR(Render_Vulkan, "XeSS: {}", text);
    } else if (level == XESS_LOGGING_LEVEL_WARNING) {
        LOG_WARNING(Render_Vulkan, "XeSS: {}", text);
    } else {
        LOG_INFO(Render_Vulkan, "XeSS: {}", text);
    }
}

bool Load() {
    static bool tried = false;
    if (tried) {
        return g_api.loaded;
    }
    tried = true;
    if (!Wanted()) {
        return false;
    }
    const auto dir = LibDir();
#ifdef _WIN32
    const auto file = dir / "libxess.dll";
#else
    const auto file = dir / "libxess.so";
#endif
    std::error_code ec;
    if (!std::filesystem::is_regular_file(file, ec)) {
        LOG_WARNING(Render_Vulkan,
                    "XeSS: {} not found - the game's own anti-aliasing stays (copy libxess.dll "
                    "from Intel's XeSS SDK, folder bin, there)",
                    file.string());
        std::printf("XeSS: %s not found - the game's own anti-aliasing stays\n",
                    file.string().c_str());
        std::fflush(stdout);
        return false;
    }
#ifdef _WIN32
    // Its own folder first for what it loads in turn.
    g_api.module = LoadLibraryExW(file.wstring().c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!g_api.module) {
        LOG_ERROR(Render_Vulkan, "XeSS: LoadLibrary({}) failed ({}) - is the Visual C++ 2015-2022 "
                                 "runtime installed?",
                  file.string(), GetLastError());
        return false;
    }
#else
    g_api.module = dlopen(file.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!g_api.module) {
        LOG_ERROR(Render_Vulkan, "XeSS: dlopen({}) failed: {}", file.string(), dlerror());
        return false;
    }
#endif
    const bool ok = Resolve(g_api.get_version, "xessGetVersion") &&
                    Resolve(g_api.instance_extensions, "xessVKGetRequiredInstanceExtensions") &&
                    Resolve(g_api.device_extensions, "xessVKGetRequiredDeviceExtensions") &&
                    Resolve(g_api.device_features, "xessVKGetRequiredDeviceFeatures") &&
                    Resolve(g_api.create_context, "xessVKCreateContext") &&
                    Resolve(g_api.build_pipelines, "xessVKBuildPipelines") &&
                    Resolve(g_api.init, "xessVKInit") && Resolve(g_api.execute, "xessVKExecute") &&
                    Resolve(g_api.destroy_context, "xessDestroyContext") &&
                    Resolve(g_api.optimal_input, "xessGetOptimalInputResolution") &&
                    Resolve(g_api.set_velocity_scale, "xessSetVelocityScale");
    Resolve(g_api.set_logging, "xessSetLoggingCallback"); // optional
    if (!ok) {
        return false;
    }
    xess_version_t version{};
    g_api.get_version(&version);
    LOG_INFO(Render_Vulkan, "XeSS: libxess {}.{}.{} loaded from {}", version.major, version.minor,
             version.patch, dir.string());
    std::printf("XeSS: libxess %u.%u.%u loaded\n", version.major, version.minor, version.patch);
    std::fflush(stdout);
    g_api.loaded = true;
    return true;
}

constexpr vk::ImageSubresourceRange ColorRange{
    .aspectMask = vk::ImageAspectFlagBits::eColor,
    .levelCount = 1,
    .layerCount = 1,
};

// Feature structures are {sType, pNext, VkBool32...}: the booleans start after the header.
constexpr size_t HeaderBytes = offsetof(VkPhysicalDeviceVulkan12Features, samplerMirrorClampToEdge);

VkBool32* Bools(void* structure) {
    return reinterpret_cast<VkBool32*>(static_cast<u8*>(structure) + HeaderBytes);
}

void OrBools(VkBool32* dst, const VkBool32* src, size_t count) {
    for (size_t i = 0; i < count; ++i) {
        dst[i] = dst[i] || src[i] ? VK_TRUE : VK_FALSE;
    }
}

template <typename Core>
constexpr size_t BoolCount() {
    return (sizeof(Core) - HeaderBytes) / sizeof(VkBool32);
}

} // namespace

bool Wanted() {
    static const bool wanted = [] {
        const char* v = std::getenv("ISSN_XESS");
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

void AddInstanceExtensions(std::vector<const char*>& extensions) {
    if (!Load()) {
        return;
    }
    uint32_t count = 0;
    const char* const* names = nullptr;
    uint32_t api_version = 0;
    const xess_result_t r = g_api.instance_extensions(&count, &names, &api_version);
    if (r != XESS_RESULT_SUCCESS) {
        LOG_WARNING(Render_Vulkan, "XeSS: instance requirements failed: {} - XeSS off", Describe(r));
        g_api.loaded = false;
        return;
    }
    for (uint32_t i = 0; i < count; ++i) {
        const char* ext = names[i];
        if (std::none_of(extensions.begin(), extensions.end(),
                         [ext](const char* e) { return std::strcmp(e, ext) == 0; })) {
            extensions.push_back(ext);
        }
    }
    LOG_INFO(Render_Vulkan, "XeSS: {} instance extensions, Vulkan {}.{} at least", count,
             VK_API_VERSION_MAJOR(api_version), VK_API_VERSION_MINOR(api_version));
}

bool DeviceExtensions(vk::Instance instance, vk::PhysicalDevice physical_device,
                      std::vector<const char*>& out) {
    if (!g_api.loaded) {
        return false;
    }
    uint32_t count = 0;
    const char* const* names = nullptr;
    const xess_result_t r = g_api.device_extensions(static_cast<VkInstance>(instance),
                                                    static_cast<VkPhysicalDevice>(physical_device),
                                                    &count, &names);
    if (r != XESS_RESULT_SUCCESS) {
        LOG_WARNING(Render_Vulkan, "XeSS: not available on this GPU / driver ({}) - the game's own "
                                   "anti-aliasing stays",
                    Describe(r));
        std::printf("XeSS: not available on this GPU / driver (%s)\n", Describe(r).c_str());
        std::fflush(stdout);
        return false;
    }
    g_api.device_exts.assign(names, names + count);
    g_api.device_query_ok = true;
    std::string list;
    for (const char* ext : g_api.device_exts) {
        list += list.empty() ? ext : std::string{", "} + ext;
    }
    LOG_INFO(Render_Vulkan, "XeSS: device extensions: {}", list.empty() ? "none" : list);
    out = g_api.device_exts;
    return true;
}

bool AddDeviceFeatures(vk::Instance instance, vk::PhysicalDevice physical_device,
                       const FeatureTargets& t) {
    if (!g_api.loaded || !g_api.device_query_ok) {
        return false;
    }
    void* chain = nullptr;
    const xess_result_t r = g_api.device_features(static_cast<VkInstance>(instance),
                                                  static_cast<VkPhysicalDevice>(physical_device),
                                                  &chain);
    if (r != XESS_RESULT_SUCCESS) {
        LOG_WARNING(Render_Vulkan, "XeSS: device features unavailable ({}) - the game's own "
                                   "anti-aliasing stays",
                    Describe(r));
        return false;
    }
    auto* v11 = reinterpret_cast<VkPhysicalDeviceVulkan11Features*>(t.v11);
    auto* v12 = reinterpret_cast<VkPhysicalDeviceVulkan12Features*>(t.v12);
    auto* v13 = reinterpret_cast<VkPhysicalDeviceVulkan13Features*>(t.v13);
    // Promoted structures: their booleans into the matching run of the 1.x structure.
    const auto into = [](const VkBaseOutStructure* src, size_t count, VkBool32* dst) {
        OrBools(dst, reinterpret_cast<const VkBool32*>(reinterpret_cast<const u8*>(src) +
                                                       HeaderBytes),
                count);
    };
    const auto ours = [&](VkStructureType type) {
        for (auto* node = static_cast<const VkBaseInStructure*>(t.create_info->pNext); node;
             node = node->pNext) {
            if (node->sType == type) {
                return true;
            }
        }
        return false;
    };
    u32 merged = 0, spliced = 0;
    for (auto* node = static_cast<VkBaseOutStructure*>(chain); node;) {
        VkBaseOutStructure* const next = node->pNext;
        switch (node->sType) {
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2:
            OrBools(reinterpret_cast<VkBool32*>(t.core),
                    reinterpret_cast<const VkBool32*>(
                        &reinterpret_cast<VkPhysicalDeviceFeatures2*>(node)->features),
                    sizeof(VkPhysicalDeviceFeatures) / sizeof(VkBool32));
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES:
            OrBools(Bools(v11), Bools(node), BoolCount<VkPhysicalDeviceVulkan11Features>());
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES:
            OrBools(Bools(v12), Bools(node), BoolCount<VkPhysicalDeviceVulkan12Features>());
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES:
            OrBools(Bools(v13), Bools(node), BoolCount<VkPhysicalDeviceVulkan13Features>());
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_16BIT_STORAGE_FEATURES:
            into(node, 4, &v11->storageBuffer16BitAccess);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MULTIVIEW_FEATURES:
            into(node, 3, &v11->multiview);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VARIABLE_POINTERS_FEATURES:
            into(node, 2, &v11->variablePointersStorageBuffer);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SAMPLER_YCBCR_CONVERSION_FEATURES:
            into(node, 1, &v11->samplerYcbcrConversion);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_DRAW_PARAMETERS_FEATURES:
            into(node, 1, &v11->shaderDrawParameters);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_8BIT_STORAGE_FEATURES:
            into(node, 3, &v12->storageBuffer8BitAccess);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_ATOMIC_INT64_FEATURES:
            into(node, 2, &v12->shaderBufferInt64Atomics);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_FLOAT16_INT8_FEATURES:
            into(node, 2, &v12->shaderFloat16);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DESCRIPTOR_INDEXING_FEATURES:
            into(node, 20, &v12->shaderInputAttachmentArrayDynamicIndexing);
            v12->descriptorIndexing = VK_TRUE;
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SCALAR_BLOCK_LAYOUT_FEATURES:
            into(node, 1, &v12->scalarBlockLayout);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGELESS_FRAMEBUFFER_FEATURES:
            into(node, 1, &v12->imagelessFramebuffer);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_UNIFORM_BUFFER_STANDARD_LAYOUT_FEATURES:
            into(node, 1, &v12->uniformBufferStandardLayout);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_SUBGROUP_EXTENDED_TYPES_FEATURES:
            into(node, 1, &v12->shaderSubgroupExtendedTypes);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SEPARATE_DEPTH_STENCIL_LAYOUTS_FEATURES:
            into(node, 1, &v12->separateDepthStencilLayouts);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_QUERY_RESET_FEATURES:
            into(node, 1, &v12->hostQueryReset);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TIMELINE_SEMAPHORE_FEATURES:
            into(node, 1, &v12->timelineSemaphore);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_BUFFER_DEVICE_ADDRESS_FEATURES:
            into(node, 3, &v12->bufferDeviceAddress);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_MEMORY_MODEL_FEATURES:
            into(node, 3, &v12->vulkanMemoryModel);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_ROBUSTNESS_FEATURES:
            into(node, 1, &v13->robustImageAccess);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_INLINE_UNIFORM_BLOCK_FEATURES:
            into(node, 2, &v13->inlineUniformBlock);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_CREATION_CACHE_CONTROL_FEATURES:
            into(node, 1, &v13->pipelineCreationCacheControl);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRIVATE_DATA_FEATURES:
            into(node, 1, &v13->privateData);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_DEMOTE_TO_HELPER_INVOCATION_FEATURES:
            into(node, 1, &v13->shaderDemoteToHelperInvocation);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_TERMINATE_INVOCATION_FEATURES:
            into(node, 1, &v13->shaderTerminateInvocation);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_SIZE_CONTROL_FEATURES:
            into(node, 2, &v13->subgroupSizeControl);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SYNCHRONIZATION_2_FEATURES:
            into(node, 1, &v13->synchronization2);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TEXTURE_COMPRESSION_ASTC_HDR_FEATURES:
            into(node, 1, &v13->textureCompressionASTC_HDR);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ZERO_INITIALIZE_WORKGROUP_MEMORY_FEATURES:
            into(node, 1, &v13->shaderZeroInitializeWorkgroupMemory);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DYNAMIC_RENDERING_FEATURES:
            into(node, 1, &v13->dynamicRendering);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_INTEGER_DOT_PRODUCT_FEATURES:
            into(node, 1, &v13->shaderIntegerDotProduct);
            break;
        case VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_4_FEATURES:
            into(node, 1, &v13->maintenance4);
            break;
        default:
            // An extension's own structure (VK_EXT_mutable_descriptor_type): into the chain as is
            // (XeSS owns its memory), unless the device chain has that structure already.
            if (ours(node->sType)) {
                LOG_WARNING(Render_Vulkan, "XeSS: feature structure {} is already in the device "
                                           "chain - left as it is",
                            int(node->sType));
            } else {
                node->pNext = static_cast<VkBaseOutStructure*>(const_cast<void*>(t.create_info->pNext));
                t.create_info->pNext = node;
                ++spliced;
            }
            node = next;
            continue;
        }
        ++merged;
        node = next;
    }
    LOG_INFO(Render_Vulkan, "XeSS: device features merged ({} into the core structures, {} added)",
             merged, spliced);
    return true;
}

struct Upscaler::Impl {
    const Instance& instance;
    Scheduler& scheduler;
    xess_context_handle_t context = nullptr;
    bool initialized = false;
    u32 out_width = 0, out_height = 0;
    u32 render_width = 0, render_height = 0;
    std::string problem;
    bool fatal = false;
    struct Owned {
        VideoCore::UniqueImage image;
        vk::UniqueImageView view;
        vk::ImageLayout layout = vk::ImageLayout::eUndefined;
        vk::Format format = vk::Format::eUndefined;
        u32 width = 0, height = 0;
    };
    Owned color_in, motion_in, depth_in, target;
    Owned color_raw, output_raw; ///< UNORM twins of sRGB images (Fsr4Aa::UnormTwin), copied raw
    std::unique_ptr<VideoCore::UniqueBuffer> depth_buffer;
    u64 depth_buffer_size = 0;
    std::deque<std::pair<u64, Owned>> retired_images;
    std::deque<std::pair<u64, std::unique_ptr<VideoCore::UniqueBuffer>>> retired_buffers;
    std::deque<std::pair<u64, u64>> timed; ///< timer slot, scheduler tick
    // GPU time of the whole XeSS pass (copies, network, result), logged as an average.
    static constexpr u32 TimerSlots = 32;
    vk::UniqueQueryPool timer_pool;
    double timer_period_ns = 0.0, timer_sum_ms = 0.0, timer_max_ms = 0.0;
    u32 timer_count = 0;
    u64 timer_next = 0; ///< advances only for timed frames, so a pending slot is never reused

    Impl(const Instance& instance_, Scheduler& scheduler_)
        : instance{instance_}, scheduler{scheduler_} {}

    ~Impl() {
        // The device is idle when the rasterizer goes (shutdown).
        if (context) {
            g_api.destroy_context(context);
        }
    }

    void Fail(std::string reason, bool permanent) {
        if (problem != reason) {
            LOG_WARNING(Render_Vulkan, "XeSS unavailable: {} - the game's own anti-aliasing stays",
                        reason);
            std::printf("XeSS: %s\n", reason.c_str());
            std::fflush(stdout);
        }
        problem = std::move(reason);
        fatal |= permanent;
    }

    static const char* QualityName(xess_quality_settings_t q) {
        switch (q) {
        case XESS_QUALITY_SETTING_AA:
            return "Native AA";
        case XESS_QUALITY_SETTING_ULTRA_QUALITY_PLUS:
            return "Ultra Quality Plus";
        case XESS_QUALITY_SETTING_ULTRA_QUALITY:
            return "Ultra Quality";
        case XESS_QUALITY_SETTING_QUALITY:
            return "Quality";
        case XESS_QUALITY_SETTING_BALANCED:
            return "Balanced";
        case XESS_QUALITY_SETTING_PERFORMANCE:
            return "Performance";
        case XESS_QUALITY_SETTING_ULTRA_PERFORMANCE:
            return "Ultra Performance";
        default:
            return "?";
        }
    }

    /// The quality mode whose input range holds the game's render size, nearest in ratio (XeSS 2:
    /// Native AA 1x, Ultra Quality Plus 1.3x, Ultra Quality 1.5x, Quality 1.7x, Balanced 2x,
    /// Performance 2.3x, Ultra Performance 3x).
    xess_quality_settings_t QualityFor(u32 rw, u32 rh, u32 ow, u32 oh) const {
        struct Mode {
            xess_quality_settings_t q;
            double ratio;
        };
        static constexpr std::array<Mode, 7> modes{{
            {XESS_QUALITY_SETTING_AA, 1.0},
            {XESS_QUALITY_SETTING_ULTRA_QUALITY_PLUS, 1.3},
            {XESS_QUALITY_SETTING_ULTRA_QUALITY, 1.5},
            {XESS_QUALITY_SETTING_QUALITY, 1.7},
            {XESS_QUALITY_SETTING_BALANCED, 2.0},
            {XESS_QUALITY_SETTING_PERFORMANCE, 2.3},
            {XESS_QUALITY_SETTING_ULTRA_PERFORMANCE, 3.0},
        }};
        const double ratio =
            std::max(double(ow) / std::max(rw, 1u), double(oh) / std::max(rh, 1u));
        const xess_2d_t output{ow, oh};
        xess_quality_settings_t best = XESS_QUALITY_SETTING_AA;
        double best_distance = 1e9;
        bool best_fits = false;
        for (const Mode& mode : modes) {
            xess_2d_t optimal{}, min{}, max{};
            const bool fits =
                g_api.optimal_input(context, &output, mode.q, &optimal, &min, &max) ==
                    XESS_RESULT_SUCCESS &&
                rw >= min.x && rw <= max.x && rh >= min.y && rh <= max.y;
            const double distance = std::abs(std::log(ratio / mode.ratio));
            if ((fits && !best_fits) || (fits == best_fits && distance < best_distance)) {
                best = mode.q;
                best_distance = distance;
                best_fits = fits;
            }
        }
        return best;
    }

    bool Create() {
        const xess_result_t r = g_api.create_context(
            static_cast<VkInstance>(instance.GetInstance()),
            static_cast<VkPhysicalDevice>(instance.GetPhysicalDevice()),
            static_cast<VkDevice>(instance.GetDevice()), &context);
        if (r != XESS_RESULT_SUCCESS || !context) {
            context = nullptr;
            Fail("context creation failed: " + Describe(r), true);
            return false;
        }
        if (g_api.set_logging) {
            g_api.set_logging(context, XESS_LOGGING_LEVEL_WARNING, &LogMessage);
        }
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
        return true;
    }

    static u32 InitFlags() {
        // The input is the game's tonemapped RGBA8 frame (LDR) and its depth is reversed.
        return XESS_INIT_FLAG_INVERTED_DEPTH | XESS_INIT_FLAG_LDR_INPUT_COLOR;
    }

    bool Init(u32 rw, u32 rh, u32 ow, u32 oh) {
        const xess_quality_settings_t quality = QualityFor(rw, rh, ow, oh);
        const auto start = std::chrono::steady_clock::now();
        // The kernels build here the first time (the driver's pipeline cache keeps them).
        if (const xess_result_t b =
                g_api.build_pipelines(context, VK_NULL_HANDLE, true, InitFlags());
            b != XESS_RESULT_SUCCESS) {
            LOG_WARNING(Render_Vulkan, "XeSS: pipeline pre-build returned {}", Describe(b));
        }
        xess_vk_init_params_t params{};
        params.outputResolution = {ow, oh};
        params.qualitySetting = quality;
        params.initFlags = InitFlags();
        params.creationNodeMask = 1;
        params.visibleNodeMask = 1;
        const xess_result_t r = g_api.init(context, &params);
        if (r != XESS_RESULT_SUCCESS) {
            // An old driver is a warning: XeSS runs.
            if (r != XESS_RESULT_WARNING_OLD_DRIVER) {
                Fail("initialization failed: " + Describe(r), true);
                return false;
            }
            LOG_WARNING(Render_Vulkan, "XeSS: the graphics driver is older than XeSS wants");
        }
        // The game's vectors are screen UV, current minus previous; XeSS wants pixels towards
        // the previous position. ISSN_XESS_MV=+ flips the sign (calibration).
        static const float mv_sign = [] {
            const char* v = std::getenv("ISSN_XESS_MV");
            return v && v[0] == '+' ? 1.0f : -1.0f;
        }();
        g_api.set_velocity_scale(context, mv_sign * float(rw), mv_sign * float(rh));
        initialized = true;
        out_width = ow;
        out_height = oh;
        render_width = rw;
        render_height = rh;
        const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                            std::chrono::steady_clock::now() - start)
                            .count();
        LOG_WARNING(Render_Vulkan, "XeSS: {} running, {}x{} -> {}x{} (ready in {} ms)",
                    QualityName(quality), rw, rh, ow, oh, ms);
        std::printf("XeSS: %s, %ux%u -> %ux%u\n", QualityName(quality), rw, rh, ow, oh);
        std::fflush(stdout);
        return true;
    }

    void ReadTimer(u64 slot) {
        std::array<u64, 2> stamps{};
        const vk::Result result = instance.GetDevice().getQueryPoolResults(
            *timer_pool, u32(slot), 2, sizeof(stamps), stamps.data(), sizeof(u64),
            vk::QueryResultFlagBits::e64);
        if (result != vk::Result::eSuccess || stamps[1] <= stamps[0]) {
            return;
        }
        const double ms = double(stamps[1] - stamps[0]) * timer_period_ns / 1e6;
        timer_sum_ms += ms;
        timer_max_ms = std::max(timer_max_ms, ms);
        if (++timer_count == 600) {
            LOG_WARNING(Render_Vulkan, "XeSS: {:.2f} ms per frame on the GPU (max {:.2f}, 600 frames)",
                        timer_sum_ms / timer_count, timer_max_ms);
            std::printf("XeSS: %.2f ms per frame on the GPU (max %.2f)\n", timer_sum_ms / timer_count,
                        timer_max_ms);
            std::fflush(stdout);
            timer_sum_ms = timer_max_ms = 0.0;
            timer_count = 0;
        }
    }

    void Retire() {
        while (!timed.empty() && scheduler.IsFree(timed.front().second)) {
            ReadTimer(timed.front().first);
            timed.pop_front();
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
        owned.view.reset();
        if (!transfer_only) {
            auto [result, view] = device.createImageViewUnique({
                .image = owned.image,
                .viewType = vk::ImageViewType::e2D,
                .format = format,
                .subresourceRange = ColorRange,
            });
            owned.view = std::move(view);
        }
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

    static constexpr vk::ImageSubresourceLayers ColorLayers{
        .aspectMask = vk::ImageAspectFlagBits::eColor,
        .mipLevel = 0,
        .baseArrayLayer = 0,
        .layerCount = 1,
    };

    static void Blit(vk::CommandBuffer cmdbuf, vk::Image source, u32 sw, u32 sh, vk::Image dest,
                     u32 dw, u32 dh) {
        const vk::ImageBlit blit{
            .srcSubresource = ColorLayers,
            .srcOffsets = std::array{vk::Offset3D{0, 0, 0}, vk::Offset3D{s32(sw), s32(sh), 1}},
            .dstSubresource = ColorLayers,
            .dstOffsets = std::array{vk::Offset3D{0, 0, 0}, vk::Offset3D{s32(dw), s32(dh), 1}},
        };
        cmdbuf.blitImage(source, vk::ImageLayout::eTransferSrcOptimal, dest,
                         vk::ImageLayout::eTransferDstOptimal, blit, vk::Filter::eNearest);
    }

    static void Copy(vk::CommandBuffer cmdbuf, vk::Image source, vk::Image dest, u32 w, u32 h) {
        cmdbuf.copyImage(source, vk::ImageLayout::eTransferSrcOptimal, dest,
                         vk::ImageLayout::eTransferDstOptimal,
                         vk::ImageCopy{.srcSubresource = ColorLayers,
                                       .dstSubresource = ColorLayers,
                                       .extent{w, h, 1}});
    }

    /// The game's images (layout TransferSrcOptimal, set by the caller) into XeSS's own, left in
    /// ShaderReadOnlyOptimal (XeSS's input layout).
    void CopyInputs(vk::CommandBuffer cmdbuf, const Fsr4Aa::Input& color,
                    const Fsr4Aa::Input& motion, const Fsr4Aa::Input& depth) {
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
        if (const vk::Format twin = Fsr4Aa::UnormTwin(color.format); twin != color.format) {
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
            To(cmdbuf, *owned, vk::ImageLayout::eShaderReadOnlyOptimal);
        }
    }

    static xess_vk_image_view_info View(Owned& owned) {
        xess_vk_image_view_info info{};
        info.imageView = *owned.view;
        info.image = static_cast<VkImage>(static_cast<vk::Image>(owned.image));
        info.subresourceRange = VkImageSubresourceRange{VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
        info.format = static_cast<VkFormat>(owned.format);
        info.width = owned.width;
        info.height = owned.height;
        return info;
    }

    bool Record(vk::CommandBuffer cmdbuf, const Fsr4Aa::Input& color, const Fsr4Aa::Input& motion,
                const Fsr4Aa::Input& depth, const Fsr4Aa::Output& out, float jx, float jy,
                bool reset) {
        const vk::Image output = out.image;
        const vk::Format output_format = out.format;
        const u32 ow = out.width, oh = out.height;
        if (fatal) {
            return false;
        }
        if (!DeviceSupported() || !g_api.loaded) {
            Fail("the GPU or its driver lacks what XeSS needs (or libxess.dll is missing)", true);
            return false;
        }
        if (!context && !Create()) {
            return false;
        }
        if (!initialized || ow != out_width || oh != out_height || color.width != render_width ||
            color.height != render_height) {
            if (initialized) {
                // A size change: XeSS's pending work must be done before it is set up again.
                scheduler.Finish();
                (void)instance.GetDevice().waitIdle();
                initialized = false;
            }
            if (!Init(color.width, color.height, ow, oh)) {
                return false;
            }
            reset = true;
        }
        Retire();

        const bool timing = timer_pool && timed.size() + 1 < TimerSlots;
        const u64 timer_slot = timing ? (timer_next++ % TimerSlots) * 2 : 0;
        if (timing) {
            cmdbuf.resetQueryPool(*timer_pool, u32(timer_slot), 2);
            // All-commands: the start waits for the game's earlier work, so only XeSS is timed.
            cmdbuf.writeTimestamp(vk::PipelineStageFlagBits::eAllCommands, *timer_pool,
                                  u32(timer_slot));
        }
        CopyInputs(cmdbuf, color, motion, depth);
        Ensure(target, vk::Format::eR16G16B16A16Sfloat, ow, oh);
        To(cmdbuf, target, vk::ImageLayout::eGeneral);

        xess_vk_execute_params_t params{};
        params.colorTexture = View(color_in);
        params.velocityTexture = View(motion_in);
        params.depthTexture = View(depth_in);
        params.outputTexture = View(target);
        params.jitterOffsetX = jx;
        params.jitterOffsetY = jy;
        params.exposureScale = 1.0f;
        params.resetHistory = reset ? 1u : 0u;
        params.inputWidth = color.width;
        params.inputHeight = color.height;
        if (const xess_result_t r =
                g_api.execute(context, static_cast<VkCommandBuffer>(cmdbuf), &params);
            r != XESS_RESULT_SUCCESS) {
            Fail("execute failed: " + Describe(r), false);
            return false;
        }

        // XeSS's RGBA16F result into the output (the resolve's target or the upscaler's scene).
        To(cmdbuf, target, vk::ImageLayout::eTransferSrcOptimal);
        Layout(cmdbuf, output, out.layout, vk::ImageLayout::eTransferDstOptimal);
        if (const vk::Format twin = Fsr4Aa::UnormTwin(output_format); twin != output_format) {
            Ensure(output_raw, twin, ow, oh, true);
            To(cmdbuf, output_raw, vk::ImageLayout::eTransferDstOptimal);
            Blit(cmdbuf, target.image, ow, oh, output_raw.image, ow, oh);
            To(cmdbuf, output_raw, vk::ImageLayout::eTransferSrcOptimal);
            Copy(cmdbuf, output_raw.image, output, ow, oh);
        } else {
            Blit(cmdbuf, target.image, ow, oh, output, ow, oh);
        }
        Layout(cmdbuf, output, vk::ImageLayout::eTransferDstOptimal, out.layout);
        if (timing) {
            cmdbuf.writeTimestamp(vk::PipelineStageFlagBits::eAllCommands, *timer_pool,
                                  u32(timer_slot) + 1);
            timed.emplace_back(timer_slot, scheduler.CurrentTick());
        }
        problem.clear();
        return true;
    }
};

Upscaler::Upscaler(const Instance& instance, Scheduler& scheduler)
    : impl{std::make_unique<Impl>(instance, scheduler)} {}

Upscaler::~Upscaler() = default;

bool Upscaler::Record(vk::CommandBuffer cmdbuf, const Fsr4Aa::Input& color,
                      const Fsr4Aa::Input& motion, const Fsr4Aa::Input& depth,
                      const Fsr4Aa::Output& output, float jitter_x, float jitter_y, bool reset) {
    return impl->Record(cmdbuf, color, motion, depth, output, jitter_x, jitter_y, reset);
}

const std::string& Upscaler::Problem() const noexcept {
    return impl->problem;
}

} // namespace Vulkan::Xess
