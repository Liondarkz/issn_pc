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

/// issn 11-05 (2): why the wanted one is not used. The manager looks the wanted id up in its table
/// (+0x30 count, +0x38 buckets); found, it waits until the world section "<world>_x_<name>" is
/// streamed in (eboot 0x3c4e40/0x3c4db0 on the table at .bss 0x7d37610); not found, it falls back.
struct TodDetail {
    bool known = false;
    uint64_t wanted = 0;
    uint32_t entries = 0;        ///< times of day in the manager's table
    bool in_table = false;       ///< the wanted id is one of them
    char name[48] = {};          ///< its name (atmosphere)
    char section[112] = {};      ///< the world section the game waits for
    int section_index = -1;      ///< -1: no such section (the game does not wait)
    int section_state = 0;       ///< 0 no section, 1 ready, 2 waiting (not streamed in), 3 no object
    uint8_t flags[4] = {};       ///< the section's bytes +0x44..+0x47
    char list[400] = {};         ///< "id name, ..." of the table (for the log)
};
TodDetail ReadTodDetail();
/// Test: ask the game for the next time of day in its table (cycles; false when the table is empty).
bool ForceNextTimeOfDay();

} // namespace IssnExposure
