// SPDX-License-Identifier: GPL-2.0-or-later
// issn 11-04: the exposure meter's diagnostic hook (src/exposure_hook.h), for the menu.
#pragma once

#include <cstdint>

namespace IssnExposure {

struct Reading {
    bool attached = false;  ///< the probe put the cave in
    float value = 0.0f;     ///< the metered value the game stored last
    float sum = 0.0f;       ///< the raw sum of the tiles
    uint32_t rows = 0;      ///< tile rows (render height / 72)
    uint32_t calls = 0;     ///< frames metered so far
    bool held = false;      ///< the menu holds the value
};

Reading Read();
/// On: the game keeps the value metered now; off: it meters again.
void Hold(bool on);
/// Called each presented frame: a log line now and then (every 5 s for 2 minutes, then every 30 s).
void Tick();

/// issn 11-05: the game's time-of-day (atmosphere) state, eboot .bss 0x7f8e800 (manager at
/// 0x59cf80): the dark saves never load a time of day's lighting files (sh-, skydome, imposter-).
struct TimeOfDay {
    bool known = false;       ///< the eboot address is set
    uint64_t wanted = 0;      ///< the id the game asks for (+0x10; ~0 = none)
    uint64_t current = 0;     ///< the id of the one in use (~0 = none)
    bool fallback = false;    ///< in use is the game's "ensured time of day" (no files of its own)
    bool lighting = false;    ///< the one in use has lighting data (what loads sh-/imposter-)
    uint64_t last_good = 0;   ///< the last id seen with lighting data this session (0 = none)
};
TimeOfDay ReadTimeOfDay();
/// Test: ask the game for the last good time of day (false when there is none).
bool ForceLastGoodTimeOfDay();

} // namespace IssnExposure
