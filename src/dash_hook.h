/* SPDX-License-Identifier: GPL-2.0-or-later */
/* inFAMOUS Second Son (CUSA00223 01.07 eboot): the dash cheats of the menu (gpu/shim/issn_hero.cpp).
 * Found 11-04 from the script bindings and the skill classes' type table (0xdac130: type id =
 * 0x2000 + index) and their factories (0xcf2da8...: DASH_SKILL 0x1ae = smoke, NEON_DASH_SKILL
 * 0x1af, TV_DASH_SKILL 0x1b0 = video). Vtable slot 32 updates a dash while it runs; the clock at
 * 0xe16290+4 counts 1440 ticks a second; the skill's start time is at +0xf0.
 *   - Neon (update 0x2ef7e0): with the dash button released the run ends; held, it ends after
 *     2.0 s unless ability 0x59 is on (0x1e2e70(&abilities, 0x59, 0)). The neon stub answers
 *     "on" for that check while the flag is set, so holding the button keeps the run going.
 *   - Video (update 0x55dd90): after 0.15 s, the dash button released or more than 1.5 s sets
 *     "end" (+0x55e). The video stub leaves out the 1.5 s while the flag is set.
 *   - Smoke (DASH_SKILL): a dash to a point picked at the start; its length is multiplied by the
 *     hero's MAX_SMOKE_DASH_RATIO (hero +0x1f7e0, 1.0 at creation, set by the game's scripts;
 *     read by 0x227ac0). The shim scales it for the long smoke dash (no hold to continue). */
#ifndef ISSN_DASH_HOOK_H
#define ISSN_DASH_HOOK_H
#include <stdint.h>
#include <string.h>

#define ISSN_DASH_SMOKE_RATIO 0x1f7e0u  /* hero: float MAX_SMOKE_DASH_RATIO */

/* Neon: lea 0xefacf8(%rip),%rdi; mov $0x59,%esi | xor %edx,%edx; call 0x1e2e70; test %al,%al;
 * je end. The stub replaces the first 12 bytes. */
#define ISSN_DASH_NEON_SITE 0x2ef9a2u
#define ISSN_DASH_NEON_SITE_LENGTH 12u
#define ISSN_DASH_NEON_BACK 0x2ef9aeu       /* xor %edx,%edx: the ability check */
#define ISSN_DASH_NEON_CONTINUE 0x2ef9c0u   /* the run goes on */
#define ISSN_DASH_ABILITIES 0xefacf8u
static const unsigned char issn_dash_neon_code[] = {
    0x48, 0x8d, 0x3d, 0x4f, 0xb3, 0xc0, 0x00, 0xbe, 0x59, 0x00, 0x00, 0x00,
    0x31, 0xd2, 0xe8, 0xbb, 0x34, 0xef, 0xff, 0x84, 0xc0, 0x74, 0x35};

/* Video: vucomiss 0xae2214(%rip),%xmm0 (1.5); ja set_end | xor $1,%al; je skip; jmp set_end */
#define ISSN_DASH_TV_SITE 0x55ddf7u
#define ISSN_DASH_TV_SITE_LENGTH 10u
#define ISSN_DASH_TV_LIMIT 0xae2214u        /* float 1.5 */
#define ISSN_DASH_TV_HELD 0x55de01u         /* xor $1,%al: the button check */
#define ISSN_DASH_TV_SET_END 0x55de11u      /* movb $1,0x55e(%r12) */
static const unsigned char issn_dash_tv_code[] = {
    0xc5, 0xf8, 0x2e, 0x05, 0x15, 0x44, 0x58, 0x00, 0x77, 0x10, 0x34, 0x01, 0x74, 0x15, 0xeb, 0x0a};

static inline int issn_dash_neon_match(const unsigned char *image, uint64_t size) {
    if (size < ISSN_DASH_NEON_SITE + sizeof(issn_dash_neon_code)) return 0;
    if (memcmp(image + ISSN_DASH_NEON_SITE, issn_dash_neon_code, sizeof(issn_dash_neon_code))) return 0;
    int32_t disp;
    memcpy(&disp, image + ISSN_DASH_NEON_SITE + 3, 4);
    return (int64_t)ISSN_DASH_NEON_SITE + 7 + disp == (int64_t)ISSN_DASH_ABILITIES;
}

static inline int issn_dash_tv_match(const unsigned char *image, uint64_t size) {
    if (size < ISSN_DASH_TV_SITE + sizeof(issn_dash_tv_code) || size < ISSN_DASH_TV_LIMIT + 4) return 0;
    if (memcmp(image + ISSN_DASH_TV_SITE, issn_dash_tv_code, sizeof(issn_dash_tv_code))) return 0;
    int32_t disp;
    memcpy(&disp, image + ISSN_DASH_TV_SITE + 4, 4);
    if ((int64_t)ISSN_DASH_TV_SITE + 8 + disp != (int64_t)ISSN_DASH_TV_LIMIT) return 0;
    float limit;
    memcpy(&limit, image + ISSN_DASH_TV_LIMIT, 4);
    return limit == 1.5f;
}

/* jmp *0(%rip) followed by its target: 14 bytes. */
static inline unsigned issn_dash_jump(unsigned char *out, const unsigned char *target) {
    const uint64_t to = (uint64_t)(uintptr_t)target;
    out[0] = 0xff; out[1] = 0x25; out[2] = out[3] = out[4] = out[5] = 0;
    memcpy(out + 6, &to, 8);
    return 14;
}

/* Neon stub. rax is free here (the check's result overwrites it); rdi and esi are the check's
 * arguments, set again for the original path. Returns the stub's length. */
static inline unsigned issn_dash_build_neon_stub(unsigned char *out, const unsigned char *image,
                                                 const volatile unsigned char *flag) {
    const uint64_t flag_at = (uint64_t)(uintptr_t)flag;
    const uint64_t abilities = (uint64_t)(uintptr_t)(image + ISSN_DASH_ABILITIES);
    unsigned n = 0;
    out[n++] = 0x48; out[n++] = 0xb8; memcpy(out + n, &flag_at, 8); n += 8;   /* movabs $flag,%rax */
    out[n++] = 0x80; out[n++] = 0x38; out[n++] = 0x00;                        /* cmpb $0,(%rax) */
    out[n++] = 0x75; const unsigned j = n++;                                  /* jne on */
    out[n++] = 0x48; out[n++] = 0xbf; memcpy(out + n, &abilities, 8); n += 8; /* movabs $abilities,%rdi */
    out[n++] = 0xbe; out[n++] = 0x59; out[n++] = 0; out[n++] = 0; out[n++] = 0; /* mov $0x59,%esi */
    n += issn_dash_jump(out + n, image + ISSN_DASH_NEON_BACK);
    out[j] = (unsigned char)(n - (j + 1));
    n += issn_dash_jump(out + n, image + ISSN_DASH_NEON_CONTINUE);            /* on: keep running */
    return n;
}

/* Video stub. al holds the button check's result (used at HELD), so the 1.5 s constant is read
 * through r11, saved around it; pop leaves the flags of vucomiss for ja. */
static inline unsigned issn_dash_build_tv_stub(unsigned char *out, const unsigned char *image,
                                               const volatile unsigned char *flag) {
    const uint64_t flag_at = (uint64_t)(uintptr_t)flag;
    const uint64_t limit = (uint64_t)(uintptr_t)(image + ISSN_DASH_TV_LIMIT);
    unsigned n = 0;
    out[n++] = 0x41; out[n++] = 0x53;                                         /* push %r11 */
    out[n++] = 0x49; out[n++] = 0xbb; memcpy(out + n, &flag_at, 8); n += 8;   /* movabs $flag,%r11 */
    out[n++] = 0x41; out[n++] = 0x80; out[n++] = 0x3b; out[n++] = 0x00;       /* cmpb $0,(%r11) */
    out[n++] = 0x75; const unsigned j_on = n++;                               /* jne on */
    out[n++] = 0x49; out[n++] = 0xbb; memcpy(out + n, &limit, 8); n += 8;     /* movabs $limit,%r11 */
    out[n++] = 0xc4; out[n++] = 0xc1; out[n++] = 0x78; out[n++] = 0x2e; out[n++] = 0x03; /* vucomiss (%r11),%xmm0 */
    out[n++] = 0x41; out[n++] = 0x5b;                                         /* pop %r11 */
    out[n++] = 0x77; out[n++] = 14;                                           /* ja set_end */
    n += issn_dash_jump(out + n, image + ISSN_DASH_TV_HELD);
    n += issn_dash_jump(out + n, image + ISSN_DASH_TV_SET_END);
    out[j_on] = (unsigned char)(n - (j_on + 1));
    out[n++] = 0x41; out[n++] = 0x5b;                                         /* on: pop %r11 */
    n += issn_dash_jump(out + n, image + ISSN_DASH_TV_HELD);                  /* only the button counts */
    return n;
}
#endif
