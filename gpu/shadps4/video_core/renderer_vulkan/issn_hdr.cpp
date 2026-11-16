// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cstdio>
#include <cstdlib>
#include "common/logging/log.h"
#include "video_core/renderer_vulkan/issn_hdr.h"
#include "video_core/renderer_vulkan/vk_dlss.h"

#ifdef _WIN32
#include <dxgi1_6.h>
#endif

namespace Vulkan::Hdr {

namespace {

float EnvFloat(const char* name, float fallback, float lo, float hi) {
    const char* v = std::getenv(name);
    if (!v || !*v) {
        return fallback;
    }
    const float f = static_cast<float>(std::atof(v));
    return (f >= lo && f <= hi) ? f : fallback;
}

/// The highest MaxLuminance among the outputs Windows runs in HDR (BT.2020 PQ); 0 = none.
float WindowsHdrPeak() {
#ifdef _WIN32
    IDXGIFactory1* factory = nullptr;
    if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory1), reinterpret_cast<void**>(&factory)))) {
        return 0.0f;
    }
    float best = 0.0f;
    IDXGIAdapter1* adapter = nullptr;
    for (UINT a = 0; factory->EnumAdapters1(a, &adapter) != DXGI_ERROR_NOT_FOUND; ++a) {
        IDXGIOutput* output = nullptr;
        for (UINT o = 0; adapter->EnumOutputs(o, &output) != DXGI_ERROR_NOT_FOUND; ++o) {
            IDXGIOutput6* output6 = nullptr;
            if (SUCCEEDED(output->QueryInterface(__uuidof(IDXGIOutput6),
                                                 reinterpret_cast<void**>(&output6)))) {
                DXGI_OUTPUT_DESC1 desc{};
                if (SUCCEEDED(output6->GetDesc1(&desc)) &&
                    desc.ColorSpace == DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020 &&
                    desc.MaxLuminance > best) {
                    best = desc.MaxLuminance;
                }
                output6->Release();
            }
            output->Release();
        }
        adapter->Release();
    }
    factory->Release();
    return best;
#else
    return 0.0f;
#endif
}

} // namespace

void Init() {
    static std::atomic<bool> done{false};
    if (done.exchange(true)) {
        return;
    }
    const char* map = std::getenv("ISSN_HDR_MAPPING");
    g_mapping.store(!(map && map[0] == '0'));
    g_env_paper_white_nits.store(EnvFloat("ISSN_HDR_PAPER_WHITE", 0.0f, 0.0f, 1000.0f));
    g_env_saturation.store(EnvFloat("ISSN_HDR_SATURATION", 1.0f, 0.0f, 2.0f));
    g_paper_white_nits.store(g_env_paper_white_nits.load());
    g_saturation.store(g_env_saturation.load());
    const float env_peak = EnvFloat("ISSN_HDR_PEAK", 0.0f, 100.0f, 10000.0f);
    if (env_peak > 0.0f) {
        g_auto_peak_nits.store(env_peak);
        g_peak_source.store("ISSN_HDR_PEAK");
    } else if (const float peak = WindowsHdrPeak(); peak >= 100.0f) {
        g_auto_peak_nits.store(peak);
        g_peak_source.store("Windows");
    }
    g_peak_nits.store(g_auto_peak_nits.load());
    LOG_WARNING(Render_Vulkan,
                "HDR mapping: {}, peak {:.0f} nits ({}), paper white {}, saturation {:.2f}",
                g_mapping.load() ? "on" : "off", g_peak_nits.load(), g_peak_source.load(),
                g_paper_white_nits.load() > 0.0f
                    ? fmt::format("{:.0f} nits", g_paper_white_nits.load())
                    : std::string("the game's"),
                g_saturation.load());
    std::printf("HDR mapping: %s, peak %.0f nits (%s)\n", g_mapping.load() ? "on" : "off",
                g_peak_nits.load(), g_peak_source.load());
    std::fflush(stdout);
}

float GamePaperWhite() {
    return Dlss::g_hdr_scale.load(std::memory_order_relaxed) * 100.0f;
}

} // namespace Vulkan::Hdr
