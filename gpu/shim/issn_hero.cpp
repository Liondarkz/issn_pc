// SPDX-License-Identifier: GPL-2.0-or-later
// issn-axy: Delsin's cheats from the in-game menu (src/hero_hook.h): infinite health (with the
// probe's SetHitPoints stub), infinite powers and the Karma Bomb meter always full. The probe
// verifies the 01.07 eboot, installs the stub and calls issn_hero_attach; a thread then follows
// the menu's switches and refills the meters of the hero the game currently has.
// 11-05: infinite health also stops the hits themselves (no gray screen), infinite missiles, and
// the Karma Bomb is the karmic streak (the old switch filled the shield).
// 11-04 (src/dash_hook.h): the neon run and the video flight while the dash button is held (two
// probe stubs read the flags below), and a long smoke dash (the hero's MAX_SMOKE_DASH_RATIO).
#include "issn_settings.h"

#include "../../src/hero_hook.h"
#include "../../src/dash_hook.h"

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <thread>

namespace {

unsigned char* image = nullptr;
// Read by the SetHitPoints stub: 1 while infinite health is on.
volatile unsigned char health_flag = 0;
// Read by the dash stubs (src/dash_hook.h): 1 while the switch is on.
volatile unsigned char neon_flag = 0, tv_flag = 0;

// The long smoke dash: the game's ratio (kept while the switch is on) times SmokeFactor. The
// value is the game's again when the switch goes off, unless the game wrote another meanwhile.
constexpr float SmokeFactor = 3.0f;
unsigned char* smoke_hero = nullptr;
float smoke_game = 1.0f, smoke_written = 0.0f;

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

void SmokeDash(unsigned char* hero, bool on) {
    if (!on || hero != smoke_hero) {
        // Off, or another hero object (a new one after loading): give the old one its value back.
        if (smoke_hero && hero == smoke_hero && Get(hero, ISSN_DASH_SMOKE_RATIO) == smoke_written) {
            std::memcpy(hero + ISSN_DASH_SMOKE_RATIO, &smoke_game, 4);
        }
        smoke_hero = nullptr;
        if (!on || !hero) {
            return;
        }
    }
    if (!hero) {
        return; // no hero yet (boot, loading): with the switch on and nothing remembered, the
                // check above let a null hero through (crash at start, 11-04)
    }
    const float now = Get(hero, ISSN_DASH_SMOKE_RATIO);
    if (!(now > 0.0f && now < 100.0f)) {
        return; // not a ratio: leave it alone
    }
    if (hero != smoke_hero || now != smoke_written) {
        smoke_hero = hero; // first tick on, or the game set a new ratio (an upgrade)
        smoke_game = now;
        smoke_written = now * SmokeFactor;
        std::memcpy(hero + ISSN_DASH_SMOKE_RATIO, &smoke_written, 4);
    }
}

std::int32_t GetInt(const unsigned char* object, std::uint32_t offset) {
    std::int32_t v;
    std::memcpy(&v, object + offset, 4);
    return v;
}

void SetInt(unsigned char* object, std::uint32_t offset, std::int32_t v) {
    std::memcpy(object + offset, &v, 4);
}

// The missiles' ammo (src/hero_hook.h): full, with the change counted as the game's own setter
// (0x1e35a0) counts it, so the HUD and the save see it.
void FillMissiles(unsigned char* hero) {
    const std::int32_t top = GetInt(hero, ISSN_HERO_HEAVY_MAX);
    unsigned char* player = image + ISSN_HERO_PLAYER;
    if (top <= 0 || top > 1000 || GetInt(player, ISSN_HERO_HEAVY) >= top) {
        return;
    }
    SetInt(player, ISSN_HERO_HEAVY, top);
    if (player[0x20ac9]) {
        SetInt(player, 0x20ad0, GetInt(player, 0x20ad0) + 1);
        if (GetInt(player, 0x20ad4) == std::int32_t(0xffffc7c0)) {
            SetInt(player, 0x20ad4, GetInt(image + ISSN_HERO_CLOCK, 0x24));
        }
        SetInt(player, 0x20acc, GetInt(player, 0x20acc) + 1);
    }
}

// The Karma Bomb (src/hero_hook.h): the karmic streak at what this side needs, ready with no time
// limit. Using the bomb clears it; the next tick (50 ms) fills it again.
void FillKarma(unsigned char* hero) {
    const std::int32_t side = GetInt(image + ISSN_HERO_PLAYER, ISSN_HERO_PLAYER_SIDE);
    if (side < 0 || side > 1) {
        return;
    }
    unsigned char* streak = hero + ISSN_HERO_STREAK;
    const std::int32_t need =
        GetInt(streak, ISSN_HERO_STREAK_REQUIRED + std::uint32_t(side) * ISSN_HERO_STREAK_SIDE_STRIDE);
    if (need <= 0 || need > 1000) {
        return; // no Karma Bomb yet (early story) or not a count
    }
    if (GetInt(streak, ISSN_HERO_STREAK_COUNT) < need) {
        SetInt(streak, ISSN_HERO_STREAK_COUNT, need);
    }
    if (GetInt(streak, ISSN_HERO_STREAK_READY_UNTIL) != 0x7fffffff) {
        SetInt(streak, ISSN_HERO_STREAK_READY_UNTIL, 0x7fffffff);
    }
}

void Tick() {
    const auto& s = IssnSettings::Get();
    health_flag = s.hero_health.load() ? 1 : 0;
    neon_flag = s.hero_neon_run.load() ? 1 : 0;
    tv_flag = s.hero_video_flight.load() ? 1 : 0;
    const bool powers = s.hero_powers.load();
    const bool karma = s.hero_karma.load() && s.hero_karma_supported.load();
    const bool missiles = s.hero_missiles.load() && s.hero_missiles_supported.load();
    const bool smoke = s.hero_smoke_dash.load();
    if (!health_flag && !powers && !karma && !missiles && !smoke && !smoke_hero) {
        return;
    }
    unsigned char* hero = Pointer(image + ISSN_HERO_GLOBAL);
    SmokeDash(hero, smoke);
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
    if (missiles) {
        FillMissiles(hero);
    }
    if (karma) {
        FillKarma(hero);
    }
}

} // namespace

extern "C" const volatile unsigned char* issn_hero_health_flag(void) {
    return &health_flag;
}

extern "C" const volatile unsigned char* issn_hero_neon_flag(void) {
    return &neon_flag;
}

extern "C" const volatile unsigned char* issn_hero_tv_flag(void) {
    return &tv_flag;
}

extern "C" void issn_hero_dash_supported(int neon, int tv) {
    IssnSettings::Get().hero_neon_supported = neon != 0;
    IssnSettings::Get().hero_video_supported = tv != 0;
}

extern "C" void issn_hero_extra_supported(int missiles, int karma, int no_hit) {
    IssnSettings::Get().hero_missiles_supported = missiles != 0;
    IssnSettings::Get().hero_karma_supported = karma != 0;
    IssnSettings::Get().hero_no_hit_supported = no_hit != 0;
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
