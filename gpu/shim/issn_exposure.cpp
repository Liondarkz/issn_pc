// SPDX-License-Identifier: GPL-2.0-or-later
// issn 11-04: the exposure meter's mailbox (src/exposure_hook.h). The game's thread writes it
// through the cave; the menu reads it and can hold the value (dark-night diagnosis).
#include "issn_exposure.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>

#include "../../src/exposure_hook.h"
#include "common/memory_patcher.h"

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

namespace {
constexpr uintptr_t TodState = 0x7f8e800; // eboot .bss: current, fallback, wanted id
std::atomic<uint64_t> g_last_good{0};

uint64_t Read64(uintptr_t address) {
    uint64_t v;
    std::memcpy(&v, reinterpret_cast<const void*>(address), 8);
    return v;
}
// A guest heap pointer: inside the 64-bit guest space the port maps (not null, aligned).
bool Plausible(uint64_t p) {
    return p >= 0x10000 && p < (1ull << 47) && (p & 7) == 0;
}
} // namespace

TimeOfDay ReadTimeOfDay() {
    TimeOfDay t;
    const uintptr_t base = MemoryPatcher::g_eboot_address;
    if (base == 0) {
        return t;
    }
    t.known = true;
    const uintptr_t state = base + TodState;
    const uint64_t current = Read64(state), fallback = Read64(state + 8);
    t.wanted = Read64(state + 0x10);
    t.current = ~0ull;
    if (Plausible(current)) {
        const uint64_t data = Read64(current + 0x28);
        if (Plausible(data)) {
            t.current = Read64(data + 0x18);
            t.lighting = Read64(data + 0x68) != 0;
        }
        t.fallback = current == fallback;
    }
    if (t.lighting && !t.fallback && t.current != ~0ull) {
        g_last_good = t.current;
    }
    t.last_good = g_last_good.load();
    return t;
}

bool ForceLastGoodTimeOfDay() {
    const TimeOfDay t = ReadTimeOfDay();
    if (!t.known || t.last_good == 0) {
        return false;
    }
    const uint64_t id = t.last_good;
    std::memcpy(reinterpret_cast<void*>(MemoryPatcher::g_eboot_address + TodState + 0x10), &id, 8);
    std::printf("Time of day: asked the game for %#llx (test)\n", static_cast<unsigned long long>(id));
    std::fflush(stdout);
    return true;
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
    const TimeOfDay t = ReadTimeOfDay();
    std::printf("Exposure meter: value %g, tile sum %g, %u tile rows, %u frames%s; time of day wanted "
                "%#llx, in use %#llx%s, lighting %s\n",
                r.value, r.sum, r.rows, r.calls, r.held ? " (held)" : "",
                static_cast<unsigned long long>(t.wanted), static_cast<unsigned long long>(t.current),
                t.fallback ? " (the game's fallback)" : "", t.lighting ? "loaded" : "NONE");
    std::fflush(stdout);
}

} // namespace IssnExposure
