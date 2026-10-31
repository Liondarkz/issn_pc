// port: shadPS4 settings used by the video core, read once from ISSN_* environment
// variables (defaults match shadPS4 except the pipeline cache, which is on).
#pragma once
#include <algorithm>
#include <cstdlib>
#include <string>
#include "common/types.h"

u32 IssnDisplayRefreshHz(); // issngpu.cpp: primary display refresh rate, 60 when unknown
bool IssnVsync();           // issngpu.cpp: issn.ini vsync
int IssnFpsLimit();         // issngpu.cpp: issn.ini fps_limit (0 = unlimited)

enum GpuReadbacksMode : int { Disabled, Relaxed, Precise };

class EmulatorSettingsImpl {
public:
    static EmulatorSettingsImpl* GetInstance() { static EmulatorSettingsImpl s; return &s; }
    static bool Flag(const char* name, bool fallback) {
        const char* v = std::getenv(name);
        return v ? (v[0] == '1' || v[0] == 'y' || v[0] == 't') : fallback;
    }
    static long Number(const char* name, long fallback) {
        const char* v = std::getenv(name);
        return v ? std::strtol(v, nullptr, 10) : fallback;
    }
    s32 GetGpuId() { static const auto value = s32(Number("ISSN_GPU_ID", -1)); return value; }
    u32 GetInternalScreenWidth() { static const auto value = u32(Number("ISSN_INTERNAL_WIDTH", 1920)); return value; }
    u32 GetInternalScreenHeight() { static const auto value = u32(Number("ISSN_INTERNAL_HEIGHT", 1080)); return value; }
    // issn-axy: ISSN_PRESENT_MODE wins; else the in-game menu's VSync (read live: the swapchain
    // checks it every 30 presents and is rebuilt when it changes):
    // on = Fifo (the display's rate), off = Immediate (Mailbox is missing on AMD's Windows driver,
    // and the core falls back from it to Fifo).
    std::string GetPresentMode() {
        if (const char* v = std::getenv("ISSN_PRESENT_MODE"); v && *v) return v;
        return IssnVsync() ? "Fifo" : "Immediate";
    }
    // port: Relaxed by default: without readbacks FaceGen reads stale GPU-written vertices
    // (vertex explosions); in the test scene it costs no measurable frame rate.
    u32 GetReadbacksMode() { static const auto value = u32(Number("ISSN_READBACKS", GpuReadbacksMode::Relaxed)); return value; }
    // issn-axy: the guest flips at most once per vblank, so the vblank rate is the frame rate cap.
    // ISSN_VBLANK_HZ (> 0) wins; else the menu's frame rate limit (issn.ini fps_limit: 30, 60, 120,
    // 240; 0 = unlimited, 240 Hz as in axylpup's build). Second Son steps its simulation by the
    // frame time, so it keeps its speed above 60 (tester: 100 FPS in the city).
    // Read live: the vblank thread follows it (driver.cpp), so the menu's limit applies at once.
    u32 GetVblankFrequency() {
        static const long forced = Number("ISSN_VBLANK_HZ", 0);
        if (forced > 0) return u32(std::max<long>(forced, 30));
        const int limit = IssnFpsLimit();
        return limit > 0 ? u32(std::max(limit, 30)) : 240u;
    }
    bool IsCopyGpuBuffers() { static const auto value = Flag("ISSN_COPY_GPU_BUFFERS", false); return value; }
    bool IsDirectMemoryAccessEnabled() { static const auto value = Flag("ISSN_DIRECT_MEMORY_ACCESS", false); return value; }
    bool IsDumpShaders() { static const auto value = Flag("ISSN_DUMP_SHADERS", false); return value; }
    bool IsHdrAllowed() { static const auto value = Flag("ISSN_HDR", false); return value; }
    bool IsNullGPU() { static const auto value = Flag("ISSN_NULL_GPU", false); return value; }
    bool IsPatchShaders() { return false; }
    bool IsPipelineCacheArchived() { return false; }
    bool IsPipelineCacheEnabled() { static const auto value = Flag("ISSN_PIPELINE_CACHE", true); return value; }
    bool IsReadbackLinearImagesEnabled() { static const auto value = Flag("ISSN_READBACK_LINEAR", false); return value; }
    bool IsRenderdocEnabled() { return false; }
    bool IsShaderCollect() { return false; }
    bool IsUserfaultfdTracking() { return false; }
    bool IsVkCrashDiagnosticEnabled() { return false; }
    bool IsVkGuestMarkersEnabled() { static const auto value = Flag("ISSN_VK_MARKERS", false); return value; }
    bool IsVkHostMarkersEnabled() { static const auto value = Flag("ISSN_VK_MARKERS", false); return value; }
    bool IsVkValidationCoreEnabled() { return true; }
    bool IsVkValidationEnabled() { static const auto value = Flag("ISSN_VK_VALIDATION", false); return value; }
    bool IsVkValidationGpuEnabled() { return false; }
    bool IsVkValidationSyncEnabled() { static const auto value = Flag("ISSN_VK_VALIDATION_SYNC", false); return value; }
    // issn-axy: the new core's presenter reads shadPS4's FSR 1 / RCAS settings (off / on, 0.25).
    bool IsFsrEnabled() { static const auto value = Flag("ISSN_FSR1", false); return value; }
    bool IsRcasEnabled() { static const auto value = Flag("ISSN_RCAS", true); return value; }
    int GetRcasAttenuation() { static const auto value = int(Number("ISSN_RCAS_ATTENUATION", 250)); return value; }
};
#define EmulatorSettings (*EmulatorSettingsImpl::GetInstance())

/// issn-axy: the Vulkan device index; SHADPS4_GPU_ID (axylpup's flag) wins over ISSN_GPU_ID.
inline s32 EffectiveGpuId() {
    if (const char* v = std::getenv("SHADPS4_GPU_ID"); v && *v) {
        return static_cast<s32>(std::strtol(v, nullptr, 10));
    }
    return EmulatorSettings.GetGpuId();
}
