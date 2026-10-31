/* SPDX-License-Identifier: GPL-2.0-or-later */
/* inFAMOUS Second Son (CUSA00223 01.07 eboot): field of view, live from the in-game menu.
 * The camera builds its view angle from four `vmulss <0.5 constant>(%rip)` (the half angle;
 * the result goes to the camera's tan at +0x5a4). AYOUB1080p's "Wider field of view" patches
 * point the four at another float holding 0.575 / 0.65; here they point at a float in a page
 * the port keeps writable, and the menu writes 0.5 x the chosen factor into it (issn_view.cpp).
 * Offsets are eboot virtual addresses (= image offsets). Picture only: the game's camera, aim
 * and culling stay as they are. */
#ifndef ISSN_FOV_HOOK_H
#define ISSN_FOV_HOOK_H
#include <stdint.h>
#include <string.h>

#define ISSN_FOV_SITE_COUNT 4u
#define ISSN_FOV_SITE_LENGTH 8u /* c5 xx 59 05 disp32 */
#define ISSN_FOV_ORIGINAL 0.5f

/* Instruction starts, their first 4 bytes and the 0.5 constant each one reads. */
static const uint32_t issn_fov_sites[ISSN_FOV_SITE_COUNT] = {0x580eb2u, 0x580f79u, 0x5805b1u, 0x5810dfu};
static const unsigned char issn_fov_opcodes[ISSN_FOV_SITE_COUNT][4] = {
    {0xc5, 0xf2, 0x59, 0x05}, /* vmulss X(%rip),%xmm1,%xmm0 */
    {0xc5, 0xfa, 0x59, 0x05}, /* vmulss X(%rip),%xmm0,%xmm0 */
    {0xc5, 0xfa, 0x59, 0x05},
    {0xc5, 0xfa, 0x59, 0x05}};
static const uint32_t issn_fov_constants[ISSN_FOV_SITE_COUNT] = {0xae4478u, 0xae4478u, 0xae446cu, 0xae448cu};

/* The four sites read their 0.5 constants (this eboot build, no field of view patch applied). */
static inline int issn_fov_signature_match(const unsigned char *image, uint64_t size) {
    for (unsigned i = 0; i < ISSN_FOV_SITE_COUNT; ++i) {
        const uint32_t at = issn_fov_sites[i];
        if ((uint64_t)at + ISSN_FOV_SITE_LENGTH > size || (uint64_t)issn_fov_constants[i] + 4 > size) return 0;
        if (memcmp(image + at, issn_fov_opcodes[i], 4)) return 0;
        int32_t disp;
        memcpy(&disp, image + at + 4, 4);
        if ((int64_t)at + ISSN_FOV_SITE_LENGTH + disp != (int64_t)issn_fov_constants[i]) return 0;
        float value;
        memcpy(&value, image + issn_fov_constants[i], 4);
        if (value != ISSN_FOV_ORIGINAL) return 0;
    }
    return 1;
}

/* Points the four sites at `value` (within rel32 reach of the image); 0 when it is not. */
static inline int issn_fov_redirect(unsigned char *image, const float *value) {
    int32_t disp[ISSN_FOV_SITE_COUNT];
    for (unsigned i = 0; i < ISSN_FOV_SITE_COUNT; ++i) {
        const int64_t delta = (int64_t)(uintptr_t)value -
                              (int64_t)(uintptr_t)(image + issn_fov_sites[i] + ISSN_FOV_SITE_LENGTH);
        if (delta < INT32_MIN || delta > INT32_MAX) return 0;
        disp[i] = (int32_t)delta;
    }
    for (unsigned i = 0; i < ISSN_FOV_SITE_COUNT; ++i)
        memcpy(image + issn_fov_sites[i] + 4, &disp[i], 4);
    return 1;
}
#endif
