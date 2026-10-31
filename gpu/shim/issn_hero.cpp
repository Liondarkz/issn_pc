// SPDX-License-Identifier: GPL-2.0-or-later
// issn-axy: Delsin's cheats from the in-game menu (src/hero_hook.h): infinite health (with the
// probe's SetHitPoints stub), infinite powers and the Karma Bomb meter always full. The probe
// verifies the 01.07 eboot, installs the stub and calls issn_hero_attach; a thread then follows
// the menu's switches and refills the meters of the hero the game currently has.
#include "issn_settings.h"

#include "../../src/hero_hook.h"

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <thread>

namespace {

unsigned char* image = nullptr;
// Read by the SetHitPoints stub: 1 while infinite health is on.
volatile unsigned char health_flag = 0;

// A pointer the game keeps, read as it is now (null when there is no hero: menus, loading).
unsigned char* Pointer(const unsigned char* at) {
    std::uintptr_t value;
    std::memcpy(&value, at, sizeof(value));
    // Guest objects live in the guest's own address space (above 4 GB, below 2^47).
    return value >= (1ull << 32) && value < (1ull << 47) ? reinterpret_cast<unsigned char*>(value) : nullptr;
}

float Get(const unsigned char* object, std::uint32_t offset) {
    float v;
    std::memcpy(&v, object + offset, 4);
    return v;
}

void Fill(unsigned char* object, std::uint32_t current, std::uint32_t maximum) {
    const float top = Get(object, maximum);
    if (top > 0.0f && top < 1.0e7f && Get(object, current) < top) {
        std::memcpy(object + current, &top, 4);
    }
}

void Tick() {
    const auto& s = IssnSettings::Get();
    health_flag = s.hero_health.load() ? 1 : 0;
    const bool powers = s.hero_powers.load(), karma = s.hero_karma.load();
    if (!health_flag && !powers && !karma) {
        return;
    }
    unsigned char* hero = Pointer(image + ISSN_HERO_GLOBAL);
    if (!hero) {
        return;
    }
    if (health_flag) {
        // Hit points lost before the switch went on come back (the stub keeps them from then).
        if (unsigned char* character = Pointer(hero + ISSN_HERO_CHARACTER)) {
            Fill(character, ISSN_HERO_HP, ISSN_HERO_HP_MAX);
        }
    }
    if (powers) {
        Fill(hero, ISSN_HERO_MAGIC, ISSN_HERO_MAGIC_MAX);
    }
    if (karma) {
        Fill(hero, ISSN_HERO_KARMA, ISSN_HERO_KARMA_MAX);
    }
}

} // namespace

extern "C" const volatile unsigned char* issn_hero_health_flag(void) {
    return &health_flag;
}

extern "C" void issn_hero_attach(unsigned char* guest_image) {
    image = guest_image;
    IssnSettings::Get().hero_supported = true;
    std::printf("Cheats: health, powers and Karma Bomb ready (Insert / R3+L2 > Game)\n");
    std::thread([] {
        for (;;) {
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
            Tick();
        }
    }).detach();
}
