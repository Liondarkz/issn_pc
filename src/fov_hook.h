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

/* The cave page (read-write): [0] 0.5 x factor (the four sites above), [1] the factor itself
 * (the readers below), then the cameras the game gave an angle (SetFov's rdi), one slot per
 * hash of the address: the game has more than one (the render camera copies the angle of the
 * gameplay one through SetFov, and recomputes only when the angle differs). */
#define ISSN_FOV_CAMERA_SLOTS 16u
typedef struct { float half_factor, factor; uint64_t camera[ISSN_FOV_CAMERA_SLOTS]; } IssnFovCave;

/* Code that reads the camera's angle (+0x590) itself, not the tan the four sites build: each gets
 * a stub that replays the read and multiplies (divides, for a vdivss) by the factor, so it agrees
 * with the wider picture. Factor 1 (Original) changes nothing. Which of them aims is not known
 * yet: ISSN_FOV_AIM (bit i = reader i) picks them, default the three next to the aspect (+0x59c). */
#define ISSN_FOV_READER_COUNT 7u
typedef struct { uint32_t at; uint8_t length, xmm, divide, ret; uint8_t code[9]; } IssnFovReader;
static const IssnFovReader issn_fov_readers[ISSN_FOV_READER_COUNT] = {
    {0x22c71bu, 8, 2, 0, 0, {0xc5, 0xfa, 0x10, 0x93, 0x90, 0x05, 0x00, 0x00}},       /* vmovss xmm2,[rbx+590h] (+59c) */
    {0x14a89bu, 8, 0, 0, 0, {0xc5, 0xfa, 0x10, 0x83, 0x90, 0x05, 0x00, 0x00}},       /* vmovss xmm0,[rbx+590h] (+59c) */
    {0x144fb3u, 9, 0, 0, 0, {0xc4, 0xc1, 0x7a, 0x10, 0x86, 0x90, 0x05, 0x00, 0x00}}, /* vmovss xmm0,[r14+590h] (+59c, +694) */
    {0x2e5460u, 9, 0, 0, 1, {0xc5, 0xfa, 0x10, 0x87, 0x90, 0x05, 0x00, 0x00, 0xc3}}, /* GetFov: vmovss xmm0,[rdi+590h]; ret */
    {0x43ac25u, 9, 0, 1, 0, {0xc4, 0xc1, 0x7a, 0x5e, 0x87, 0x90, 0x05, 0x00, 0x00}}, /* vdivss xmm0,xmm0,[r15+590h] */
    {0x133159u, 8, 2, 0, 0, {0xc5, 0xfa, 0x10, 0x93, 0x90, 0x05, 0x00, 0x00}},       /* vmovss xmm2,[rbx+590h] */
    {0x145745u, 8, 0, 0, 0, {0xc5, 0xfa, 0x10, 0x80, 0x90, 0x05, 0x00, 0x00}},       /* vmovss xmm0,[rax+590h] */
};
#define ISSN_FOV_AIM_DEFAULT 0x0u /* none: the aim is the two sites below (tester 10-31) */

/* The aim: two places build the shot's direction through the reticle from the field of view the
 * controller hands the camera (+0x468 + +0x358), not from the camera: tan(0.5 x angle) x the
 * camera's aspect x the reticle's screen position. The reticle sits above the centre, so with
 * only the picture wider the shots landed below it (1.15x a little, 1.4x more). Their 0.5 goes to
 * the same cave float as the camera's four sites. */
#define ISSN_FOV_AIM_SITE_COUNT 2u
static const uint32_t issn_fov_aim_sites[ISSN_FOV_AIM_SITE_COUNT] = {0x14ebf2u, 0x14a5e9u};
static const unsigned char issn_fov_aim_opcodes[ISSN_FOV_AIM_SITE_COUNT][4] = {
    {0xc5, 0xfa, 0x59, 0x05}, /* vmulss xmm0,xmm0,[rip+X] */
    {0xc5, 0xea, 0x59, 0x05}};/* vmulss xmm0,xmm2,[rip+X] */
static const uint32_t issn_fov_aim_constants[ISSN_FOV_AIM_SITE_COUNT] = {0x9960a4u, 0x996040u};

/* Points the aim sites at `value`; returns how many were (0 when the code differs). */
static inline unsigned issn_fov_redirect_aim(unsigned char *image, uint64_t size, const float *value) {
    unsigned done = 0;
    for (unsigned i = 0; i < ISSN_FOV_AIM_SITE_COUNT; ++i) {
        const uint32_t at = issn_fov_aim_sites[i];
        if ((uint64_t)at + ISSN_FOV_SITE_LENGTH > size || memcmp(image + at, issn_fov_aim_opcodes[i], 4)) continue;
        int32_t disp;
        memcpy(&disp, image + at + 4, 4);
        if ((int64_t)at + ISSN_FOV_SITE_LENGTH + disp != (int64_t)issn_fov_aim_constants[i]) continue;
        float original;
        memcpy(&original, image + issn_fov_aim_constants[i], 4);
        if (original != ISSN_FOV_ORIGINAL) continue;
        const int64_t delta = (int64_t)(uintptr_t)value - (int64_t)(uintptr_t)(image + at + ISSN_FOV_SITE_LENGTH);
        if (delta < INT32_MIN || delta > INT32_MAX) continue;
        disp = (int32_t)delta;
        memcpy(image + at + 4, &disp, 4);
        ++done;
    }
    return done;
}

/* SetFov(camera, angle) at 0x580580: its first two instructions after the prologue are replayed
 * after `mov [camera slot], rdi`, so the menu can ask that camera to work its angle out again. */
#define ISSN_FOV_SETFOV_SITE 0x580586u
#define ISSN_FOV_SETFOV_LENGTH 11u
static const unsigned char issn_fov_setfov_code[ISSN_FOV_SETFOV_LENGTH] = {
    0x48, 0x89, 0xfb, 0xc5, 0xfa, 0x11, 0x83, 0xd4, 0x05, 0x00, 0x00}; /* mov rbx,rdi; vmovss [rbx+5d4h],xmm0 */

static inline int issn_rel32(const unsigned char *next, const void *target, int32_t *out) {
    const int64_t d = (int64_t)(uintptr_t)target - (int64_t)(uintptr_t)next;
    if (d < INT32_MIN || d > INT32_MAX) return 0;
    *out = (int32_t)d;
    return 1;
}

/* A reader's stub at `out`: the read, vmulss/vdivss xmmN,xmmN,[rip -> factor], then ret (GetFov)
 * or a jmp back. Returns its length, 0 when out of reach. */
static inline unsigned issn_fov_build_reader(unsigned char *out, const unsigned char *image,
                                             const IssnFovReader *r, const float *factor) {
    unsigned n = 0;
    const unsigned body = r->ret ? r->length - 1u : r->length;
    memcpy(out, r->code, body); n += body;
    out[n++] = 0xc5;
    out[n++] = (uint8_t)(0x80 | ((~r->xmm & 15) << 3) | 0x02); /* R=1, vvvv=xmmN, L=0, F3 */
    out[n++] = r->divide ? 0x5e : 0x59;
    out[n++] = (uint8_t)((r->xmm & 7) << 3 | 5);              /* [rip+disp32] */
    int32_t d;
    if (!issn_rel32(out + n + 4, factor, &d)) return 0;
    memcpy(out + n, &d, 4); n += 4;
    if (r->ret) { out[n++] = 0xc3; return n; }
    out[n++] = 0xe9;
    if (!issn_rel32(out + n + 4, image + r->at + r->length, &d)) return 0;
    memcpy(out + n, &d, 4); n += 4;
    return n;
}

/* SetFov's stub: push rax/rcx; rax = (rdi >> 6) & 15; rcx = &slots; mov [rcx+rax*8], rdi;
 * pop; the two replaced instructions; jmp back. Flags are dead here (the vucomiss after the
 * replayed store sets them) and SetFov has its frame, so the pushes below rsp are free. */
static inline unsigned issn_fov_build_setfov(unsigned char *out, const unsigned char *image, uint64_t *slots) {
    static const unsigned char head[] = {
        0x50, 0x51,                   /* push rax; push rcx */
        0x48, 0x89, 0xf8,             /* mov rax, rdi */
        0x48, 0xc1, 0xe8, 0x06,       /* shr rax, 6 */
        0x83, 0xe0, 0x0f,             /* and eax, 15 */
        0x48, 0x8d, 0x0d};            /* lea rcx, [rip+disp32] */
    static const unsigned char tail[] = {
        0x48, 0x89, 0x3c, 0xc1,       /* mov [rcx+rax*8], rdi */
        0x59, 0x58};                  /* pop rcx; pop rax */
    unsigned n = 0;
    int32_t d;
    memcpy(out, head, sizeof head); n += sizeof head;
    if (!issn_rel32(out + n + 4, slots, &d)) return 0;
    memcpy(out + n, &d, 4); n += 4;
    memcpy(out + n, tail, sizeof tail); n += sizeof tail;
    memcpy(out + n, issn_fov_setfov_code, ISSN_FOV_SETFOV_LENGTH); n += ISSN_FOV_SETFOV_LENGTH;
    out[n++] = 0xe9;
    if (!issn_rel32(out + n + 4, image + ISSN_FOV_SETFOV_SITE + ISSN_FOV_SETFOV_LENGTH, &d)) return 0;
    memcpy(out + n, &d, 4); n += 4;
    return n;
}
#endif
