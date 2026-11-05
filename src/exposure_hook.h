/* SPDX-License-Identifier: GPL-2.0-or-later */
/* inFAMOUS Second Son (CUSA00223 01.07 eboot): the exposure meter, for the dark-night diagnosis
 * (11-04). The CPU function at 0x171610 sums the GPU's per-tile luminance (one float per 128x72
 * pixels, from the buffer the GPU filled a frame before), turns the sum into the metered value
 * (call 0x984530) and stores it through r12 at 0x17187d. A cave there copies the value, the raw
 * sum and the tile rows into a mailbox the in-game menu shows, and, while the menu's "hold"
 * switch is on, stores the held value instead (the exposure stops following the scene: a test
 * of whether the darkness comes from the metering). Offsets are eboot virtual addresses; the
 * hook goes in only when the bytes match. */
#ifndef ISSN_EXPOSURE_HOOK_H
#define ISSN_EXPOSURE_HOOK_H
#include <stdint.h>

#define ISSN_EXPOSURE_SITE 0x17187du    /* vmovss %xmm0,(%r12); mov %bl,%al (8 bytes) */
#define ISSN_EXPOSURE_SITE_LENGTH 8u
#define ISSN_EXPOSURE_EXPECTED_AT 0x171878u
static const unsigned char issn_exposure_expected[13] = {
    0xe8, 0xb3, 0x2c, 0x81, 0x00,       /* call 0x984530 */
    0xc4, 0xc1, 0x7a, 0x11, 0x04, 0x24, /* vmovss %xmm0,(%r12) */
    0x88, 0xd8,                         /* mov %bl,%al */
};

/* rcx and rdx are free at the site (the function returns right after; al is the result). */
static const unsigned char issn_exposure_cave[63] = {
    0x48, 0xb9, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, /* movabs $mailbox,%rcx */
    0x8b, 0x51, 0x10,                                           /* mov 0x10(%rcx),%edx (held) */
    0x85, 0xd2,                                                 /* test %edx,%edx */
    0x74, 0x04,                                                 /* je 1f */
    0xc5, 0xf9, 0x6e, 0xc2,                                     /* vmovd %edx,%xmm0 */
    0xc4, 0xc1, 0x7a, 0x11, 0x04, 0x24,                         /* 1: vmovss %xmm0,(%r12) */
    0x88, 0xd8,                                                 /* mov %bl,%al */
    0xc5, 0xfa, 0x11, 0x01,                                     /* vmovss %xmm0,(%rcx) */
    0x8b, 0x54, 0x24, 0x40,                                     /* mov 0x40(%rsp),%edx (sum) */
    0x89, 0x51, 0x04,                                           /* mov %edx,4(%rcx) */
    0x8b, 0x14, 0x24,                                           /* mov (%rsp),%edx (tile rows) */
    0x89, 0x51, 0x08,                                           /* mov %edx,8(%rcx) */
    0xff, 0x41, 0x0c,                                           /* incl 12(%rcx) */
    0xff, 0x25, 0x00, 0x00, 0x00, 0x00,                         /* jmp *0(%rip) */
    0x22, 0x22, 0x22, 0x22, 0x22, 0x22, 0x22, 0x22,             /* return address */
};
#define ISSN_EXPOSURE_PATCH_MAILBOX 2u

/* The mailbox (gpu/shim/issngpu.cpp): written by the game's thread, read by the menu. */
typedef struct {
    float value;       /* the metered value the game stored */
    float sum;         /* the raw sum of the tiles */
    uint32_t rows;     /* tile rows (render height / 72) */
    uint32_t calls;    /* frames metered */
    uint32_t held;     /* nonzero: the bits of the value stored instead */
} IssnExposureMailbox;

#endif
