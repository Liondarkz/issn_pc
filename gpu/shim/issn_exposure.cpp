// SPDX-License-Identifier: GPL-2.0-or-later
// issn 11-04: the exposure meter's mailbox (src/exposure_hook.h). The game's thread writes it
// through the cave; the menu reads it and can hold the value (dark-night diagnosis).
#include "issn_exposure.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>

#include "../../src/exposure_hook.h"

namespace {
alignas(64) IssnExposureMailbox g_box{};
std::atomic<bool> g_attached{false};

float Load(const float& f) {
    float v;
    std::memcpy(&v, const_cast<const float*>(&f), sizeof(v));
    return v;
}
} // namespace

extern "C" void* issngpu_exposure_mailbox(void) {
    return &g_box;
}

extern "C" void issngpu_exposure_attached(void) {
    g_attached = true;
}

namespace IssnExposure {

Reading Read() {
    Reading r;
    r.attached = g_attached.load();
    const volatile IssnExposureMailbox& box = g_box;
    r.value = Load(const_cast<const IssnExposureMailbox&>(box).value);
    r.sum = Load(const_cast<const IssnExposureMailbox&>(box).sum);
    r.rows = box.rows;
    r.calls = box.calls;
    r.held = box.held != 0;
    return r;
}

void Hold(bool on) {
    volatile IssnExposureMailbox& box = g_box;
    if (!on) {
        box.held = 0;
        std::printf("Exposure meter: following the scene again\n");
    } else {
        float v = Read().value;
        uint32_t bits;
        std::memcpy(&bits, &v, 4);
        box.held = bits ? bits : 1u;
        std::printf("Exposure meter: held at %g\n", v);
    }
    std::fflush(stdout);
}

void Tick() {
    if (!g_attached.load(std::memory_order_relaxed)) {
        return;
    }
    using Clock = std::chrono::steady_clock;
    static const auto start = Clock::now();
    static auto next = start + std::chrono::seconds(5);
    const auto now = Clock::now();
    if (now < next) {
        return;
    }
    const bool early = now - start < std::chrono::minutes(2);
    next = now + std::chrono::seconds(early ? 5 : 30);
    const Reading r = Read();
    std::printf("Exposure meter: value %g, tile sum %g, %u tile rows, %u frames%s\n", r.value, r.sum,
                r.rows, r.calls, r.held ? " (held)" : "");
    std::fflush(stdout);
}

} // namespace IssnExposure
