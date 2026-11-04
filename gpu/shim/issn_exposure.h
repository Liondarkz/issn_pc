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

} // namespace IssnExposure
