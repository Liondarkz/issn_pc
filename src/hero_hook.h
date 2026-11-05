/* SPDX-License-Identifier: GPL-2.0-or-later */
/* inFAMOUS Second Son (CUSA00223 01.07 eboot): Delsin's health, powers and Karma Bomb meter for
 * the menu's cheats (gpu/shim/issn_hero.cpp). Offsets are eboot virtual addresses (= image
 * offsets), found in the 01.07 eboot (10-31) from the script bindings CURRENT_HIT_POINTS,
 * ADJUST_MAGIC_POINTS, MAX_HEAVY_MAGIC_POINTS and their native functions:
 *   - a global holds the hero (the player's controller object); hero+0x38 is his character;
 *   - character: hit points at +0xc04, the maximum at +0xc00. Every change goes through
 *     SetHitPoints(character, float hp, source) at 0x4f38b0 (clamps to >= 0, stores, runs death);
 *   - hero: power energy ("magic") at +0x1a1e8, its maximum at +0x1a1f0 (AdjustMagic 0x214b10 and
 *     the cast debit 0x20c930 clamp between them).
 * 11-05: +0x1ee50/+0x1ee54, filled before as "the Karma Bomb meter", are CURRENT_SHIELD_POINTS and
 * MAX_SHIELD_POINTS (the binding table's data sits in the entry before each name); the Karma Bomb
 * is the karmic streak below (João 11-05: the old switch did not keep it ready).
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
/* The Karma Bomb: the karmic streak object at hero +0x1ef30 (KARMIC_STREAK_CURRENT 0x2e43c0 reads
 * its count at +0x28; KARMIC_STREAK_REQUIRED 0x266b00 reads +0x60 + side * 0x28, the side being the
 * player state's +0xc4, 0 or 1). When the count reaches it, 0x266310 sets +0x38 to the clock time
 * the bomb stays ready until (0x7fffffff: no limit) and IS_IONIC_AVAILABLE (0x2e43a0) is "+0x38
 * later than now"; using the bomb calls 0x266310 with 0, which clears both. */
#define ISSN_HERO_STREAK 0x1ef30u
#define ISSN_HERO_STREAK_COUNT 0x28u
#define ISSN_HERO_STREAK_READY_UNTIL 0x38u
#define ISSN_HERO_STREAK_REQUIRED 0x60u
#define ISSN_HERO_STREAK_SIDE_STRIDE 0x28u
#define ISSN_HERO_PLAYER_SIDE 0xc4u
#define ISSN_HERO_STREAK_GETTER 0x2e43c0u
#define ISSN_HERO_STREAK_REQUIRED_GETTER 0x266b00u
static const unsigned char issn_hero_streak_getter_code[] = {0x8b, 0x87, 0x58, 0xef, 0x01, 0x00, 0xc3};
/* lea X(%rip),%rax (X = the player state); movslq 0xc4(%rax),%rcx; xor %eax,%eax; cmp $1,%rcx; ja;
 * lea (%rcx,%rcx,4),%rax; mov 0x60(%rdi,%rax,8),%eax */
static const unsigned char issn_hero_streak_required_tail[] = {
    0x48, 0x63, 0x88, 0xc4, 0x00, 0x00, 0x00, 0x31, 0xc0, 0x48, 0x83, 0xf9, 0x01, 0x77, 0x08,
    0x48, 0x8d, 0x04, 0x89, 0x8b, 0x44, 0xc7, 0x60};

/* 11-05: the missiles' ammo ("heavy magic") is an int in the player state (bss 0xefacf8 + 0xdc;
 * getter 0x214e40, setter 0x1e35a0, which also counts the change in +0x20acc for the save); the
 * hero holds its maximum (+0x1ee4c). DebitMagicForCast (0x20c740) takes each power's heavy cost
 * (+0x1a204 per power slot) from it, so infinite powers did not cover the missiles. */
#define ISSN_HERO_PLAYER 0xefacf8u      /* player state (bss) */
#define ISSN_HERO_HEAVY 0xdcu           /* player state: int missiles left */
#define ISSN_HERO_HEAVY_MAX 0x1ee4cu    /* hero: int maximum */
#define ISSN_HERO_HEAVY_GETTER 0x214e40u
#define ISSN_HERO_HEAVY_SETTER 0x1e35a0u
#define ISSN_HERO_CLOCK 0xe16290u       /* the game clock (+0x24 is read by the setter) */
/* lea X(%rip),%rax (X = the player state); mov 0xdc(%rax),%eax; ret */
static const unsigned char issn_hero_heavy_getter_tail[] = {0x8b, 0x80, 0xdc, 0x00, 0x00, 0x00, 0xc3};
/* cmp %esi,0xdc(%rdi); je; mov %esi,0xdc(%rdi); cmpb $0,0x20ac9(%rdi) */
static const unsigned char issn_hero_heavy_setter_code[] = {
    0x39, 0xb7, 0xdc, 0x00, 0x00, 0x00, 0x74, 0x43, 0x89, 0xb7, 0xdc, 0x00, 0x00, 0x00,
    0x80, 0xbf, 0xc9, 0x0a, 0x02, 0x00, 0x00};

static inline int issn_hero_heavy_match(const unsigned char *image, uint64_t size) {
    if (size < ISSN_HERO_PLAYER + 0x20ad8) return 0;
    if (memcmp(image + ISSN_HERO_HEAVY_GETTER, "\x48\x8d\x05", 3) ||
        memcmp(image + ISSN_HERO_HEAVY_GETTER + 7, issn_hero_heavy_getter_tail,
               sizeof(issn_hero_heavy_getter_tail))) return 0;
    int32_t disp;
    memcpy(&disp, image + ISSN_HERO_HEAVY_GETTER + 3, 4);
    if ((int64_t)ISSN_HERO_HEAVY_GETTER + 7 + disp != (int64_t)ISSN_HERO_PLAYER) return 0;
    return !memcmp(image + ISSN_HERO_HEAVY_SETTER, issn_hero_heavy_setter_code,
                   sizeof(issn_hero_heavy_setter_code));
}

static inline int issn_hero_karma_match(const unsigned char *image, uint64_t size) {
    if (size < ISSN_HERO_PLAYER + 0x100) return 0;
    if (memcmp(image + ISSN_HERO_STREAK_GETTER, issn_hero_streak_getter_code,
               sizeof(issn_hero_streak_getter_code))) return 0;
    if (memcmp(image + ISSN_HERO_STREAK_REQUIRED_GETTER, "\x48\x8d\x05", 3) ||
        memcmp(image + ISSN_HERO_STREAK_REQUIRED_GETTER + 7, issn_hero_streak_required_tail,
               sizeof(issn_hero_streak_required_tail))) return 0;
    int32_t disp;
    memcpy(&disp, image + ISSN_HERO_STREAK_REQUIRED_GETTER + 3, 4);
    return (int64_t)ISSN_HERO_STREAK_REQUIRED_GETTER + 7 + disp == (int64_t)ISSN_HERO_PLAYER;
}

/* 11-05: a hit reaches a character through its slot-60 method (0x4f7520, the same for the
 * hero's character class, vtable 0xcdb7d0, and the plain one): it notes the hit time (+0x1afc),
 * adds the damage share (+0x1b00), calls AdjustHitPoints (0x4f4380: the recent-damage history at
 * +0xc20 and the hit time +0xc18, then SetHitPoints) and sends the damage response (0x46aa00).
 * The SetHitPoints stub kept the health, but the rest still ran (the gray screen). With infinite
 * health the damage stub returns at once for the hero's character: the hit does not happen. */
#define ISSN_HERO_TAKE_HIT 0x4f7520u
#define ISSN_HERO_TAKE_HIT_LENGTH 10u  /* push rbp; mov rsp,rbp; push r15; push r14; push r12 */
static const unsigned char issn_hero_take_hit_code[] = {
    0x55, 0x48, 0x89, 0xe5, 0x41, 0x57, 0x41, 0x56, 0x41, 0x54, 0x53, 0x48, 0x83, 0xec, 0x20,
    0x4c, 0x8b, 0x25};

static inline int issn_hero_take_hit_match(const unsigned char *image, uint64_t size) {
    return size >= ISSN_HERO_TAKE_HIT + sizeof(issn_hero_take_hit_code) &&
           !memcmp(image + ISSN_HERO_TAKE_HIT, issn_hero_take_hit_code, sizeof(issn_hero_take_hit_code));
}

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
/* The damage stub at the slot-60 method: with the flag on and rdi the hero's character, return
 * at once (the method returns nothing); otherwise the replaced instructions and a jump back. rax
 * is free at a function's start. Returns the stub's length. */
static inline unsigned issn_hero_build_damage_stub(unsigned char *out, const unsigned char *image,
                                                   const volatile unsigned char *flag) {
    const uint64_t global = (uint64_t)(uintptr_t)(image + ISSN_HERO_GLOBAL);
    const uint64_t flag_at = (uint64_t)(uintptr_t)flag;
    const uint64_t back = (uint64_t)(uintptr_t)(image + ISSN_HERO_TAKE_HIT + ISSN_HERO_TAKE_HIT_LENGTH);
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
    out[n++] = 0xc3;                                                       /* ret: no hit */
    const unsigned replay = n;
    out[j1] = (unsigned char)(replay - (j1 + 1));
    out[j2] = (unsigned char)(replay - (j2 + 1));
    out[j3] = (unsigned char)(replay - (j3 + 1));
    memcpy(out + n, image + ISSN_HERO_TAKE_HIT, ISSN_HERO_TAKE_HIT_LENGTH); n += ISSN_HERO_TAKE_HIT_LENGTH;
    out[n++] = 0xff; out[n++] = 0x25; out[n++] = 0; out[n++] = 0; out[n++] = 0; out[n++] = 0; /* jmp *0(%rip) */
    memcpy(out + n, &back, 8); n += 8;
    return n;
}
#endif
