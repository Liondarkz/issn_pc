/* SPDX-License-Identifier: GPL-2.0-or-later */
/* inFAMOUS Second Son (CUSA00223 01.07 eboot): Delsin's health, powers and Karma Bomb meter for
 * the menu's cheats (gpu/shim/issn_hero.cpp). Offsets are eboot virtual addresses (= image
 * offsets), found in the 01.07 eboot (10-31) from the script bindings CURRENT_HIT_POINTS,
 * ADJUST_MAGIC_POINTS, MAX_HEAVY_MAGIC_POINTS and their native functions:
 *   - a global holds the hero (the player's controller object); hero+0x38 is his character;
 *   - character: hit points at +0xc04, the maximum at +0xc00. Every change goes through
 *     SetHitPoints(character, float hp, source) at 0x4f38b0 (clamps to >= 0, stores, runs death);
 *   - hero: power energy ("magic") at +0x1a1e8, its maximum at +0x1a1f0 (AdjustMagic 0x214b10 and
 *     the cast debit 0x20c930 clamp between them); the Karma Bomb meter ("heavy magic") at
 *     +0x1ee50, its maximum at +0x1ee54.
 * Infinite health: a stub at SetHitPoints keeps the hero's character from losing hit points
 * (xmm0 = max(xmm0, current)) while the flag is on; the shim also refills both meters. */
#ifndef ISSN_HERO_HOOK_H
#define ISSN_HERO_HOOK_H
#include <stdint.h>
#include <string.h>

#define ISSN_HERO_GLOBAL 0x1d0cfe0u  /* the hero controller pointer (bss) */
#define ISSN_HERO_CHARACTER 0x38u    /* hero -> character */
#define ISSN_HERO_HP 0xc04u          /* character: float hit points */
#define ISSN_HERO_HP_MAX 0xc00u      /* character: float maximum */
#define ISSN_HERO_MAGIC 0x1a1e8u     /* hero: float power energy */
#define ISSN_HERO_MAGIC_MAX 0x1a1f0u
#define ISSN_HERO_KARMA 0x1ee50u     /* hero: float Karma Bomb meter */
#define ISSN_HERO_KARMA_MAX 0x1ee54u

#define ISSN_HERO_SET_HP 0x4f38b0u   /* SetHitPoints */
#define ISSN_HERO_SITE 0x4f38beu     /* the 10 bytes the stub replays */
#define ISSN_HERO_SITE_LENGTH 10u

/* push rbp ... push rax; vxorps xmm2,xmm2,xmm2; mov r12,rdi; mov r15,rsi; vmaxss xmm0,xmm2,xmm0;
 * vmovss xmm1,0xc04(%r12) */
static const unsigned char issn_hero_set_hp_code[] = {
    0x55, 0x48, 0x89, 0xe5, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x53, 0x50,
    0xc5, 0xe8, 0x57, 0xd2, 0x49, 0x89, 0xfc, 0x49, 0x89, 0xf7, 0xc5, 0xea, 0x5f, 0xc0,
    0xc4, 0xc1, 0x7a, 0x10, 0x8c, 0x24, 0x04, 0x0c, 0x00, 0x00};
/* AdjustMagic: vmovss 0x1a1e8(%rdi),%xmm2; vmovss 0x1a1f0(%rdi),%xmm1 */
#define ISSN_HERO_ADJUST_MAGIC_LOADS 0x214b35u
static const unsigned char issn_hero_magic_code[] = {
    0xc5, 0xfa, 0x10, 0x97, 0xe8, 0xa1, 0x01, 0x00, 0xc5, 0xfa, 0x10, 0x8f, 0xf0, 0xa1, 0x01, 0x00};
/* A reader of the hero global: lea X(%rip),%rax (X = the global); mov (%rax),%rdi;
 * vmovss 0x1a1f0(%rdi),%xmm0 */
#define ISSN_HERO_GLOBAL_READER 0x2659a5u
static const unsigned char issn_hero_reader_code[] = {0x48, 0x8d, 0x05};
static const unsigned char issn_hero_reader_tail[] = {0x48, 0x8b, 0x38, 0xc5, 0xfa, 0x10, 0x87, 0xf0, 0xa1, 0x01, 0x00};

static inline int issn_hero_signature_match(const unsigned char *image, uint64_t size) {
    if (size < ISSN_HERO_GLOBAL + 8) return 0;
    if (memcmp(image + ISSN_HERO_SET_HP, issn_hero_set_hp_code, sizeof(issn_hero_set_hp_code))) return 0;
    if (memcmp(image + ISSN_HERO_ADJUST_MAGIC_LOADS, issn_hero_magic_code, sizeof(issn_hero_magic_code))) return 0;
    if (memcmp(image + ISSN_HERO_GLOBAL_READER, issn_hero_reader_code, 3) ||
        memcmp(image + ISSN_HERO_GLOBAL_READER + 7, issn_hero_reader_tail, sizeof(issn_hero_reader_tail))) return 0;
    int32_t disp;
    memcpy(&disp, image + ISSN_HERO_GLOBAL_READER + 3, 4);
    return (int64_t)ISSN_HERO_GLOBAL_READER + 7 + disp == (int64_t)ISSN_HERO_GLOBAL;
}

/* The SetHitPoints stub: when the flag is on and rdi is the hero's character, xmm0 (the new hit
 * points) is raised to the current ones; then the replaced instructions and a jump back.
 * rax is free here (the function writes it before reading it). Returns the stub's length. */
static inline unsigned issn_hero_build_stub(unsigned char *out, const unsigned char *image,
                                            const volatile unsigned char *flag) {
    const uint64_t global = (uint64_t)(uintptr_t)(image + ISSN_HERO_GLOBAL);
    const uint64_t flag_at = (uint64_t)(uintptr_t)flag;
    const uint64_t back = (uint64_t)(uintptr_t)(image + ISSN_HERO_SITE + ISSN_HERO_SITE_LENGTH);
    unsigned n = 0;
    out[n++] = 0x48; out[n++] = 0xb8; memcpy(out + n, &flag_at, 8); n += 8; /* movabs $flag,%rax */
    out[n++] = 0x80; out[n++] = 0x38; out[n++] = 0x00;                     /* cmpb $0,(%rax) */
    out[n++] = 0x74; const unsigned j1 = n++;                              /* je replay */
    out[n++] = 0x48; out[n++] = 0xb8; memcpy(out + n, &global, 8); n += 8; /* movabs $global,%rax */
    out[n++] = 0x48; out[n++] = 0x8b; out[n++] = 0x00;                     /* mov (%rax),%rax */
    out[n++] = 0x48; out[n++] = 0x85; out[n++] = 0xc0;                     /* test %rax,%rax */
    out[n++] = 0x74; const unsigned j2 = n++;                              /* je replay */
    out[n++] = 0x48; out[n++] = 0x8b; out[n++] = 0x40; out[n++] = ISSN_HERO_CHARACTER; /* mov 0x38(%rax),%rax */
    out[n++] = 0x48; out[n++] = 0x39; out[n++] = 0xf8;                     /* cmp %rdi,%rax */
    out[n++] = 0x75; const unsigned j3 = n++;                              /* jne replay */
    static const unsigned char raise[] = {0xc5, 0xfa, 0x5f, 0x87, 0x04, 0x0c, 0x00, 0x00}; /* vmaxss 0xc04(%rdi),%xmm0,%xmm0 */
    memcpy(out + n, raise, sizeof(raise)); n += sizeof(raise);
    const unsigned replay = n;
    out[j1] = (unsigned char)(replay - (j1 + 1));
    out[j2] = (unsigned char)(replay - (j2 + 1));
    out[j3] = (unsigned char)(replay - (j3 + 1));
    memcpy(out + n, image + ISSN_HERO_SITE, ISSN_HERO_SITE_LENGTH); n += ISSN_HERO_SITE_LENGTH;
    out[n++] = 0xff; out[n++] = 0x25; out[n++] = 0; out[n++] = 0; out[n++] = 0; out[n++] = 0; /* jmp *0(%rip) */
    memcpy(out + n, &back, 8); n += 8;
    return n;
}
#endif
