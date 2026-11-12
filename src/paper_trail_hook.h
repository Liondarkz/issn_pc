/* SPDX-License-Identifier: GPL-2.0-or-later */
/* inFAMOUS Second Son (01.07 eboot): Paper Trail without its website (11-05).
 * Paper Trail's missions were unlocked by infamouspapertrail.com, offline since 2018. The game
 * POSTs {"onlineId": ...} to https://api.infamouspapertrail.com/update and expects 48 values 0/1
 * (w1m1es ... w6m8w: weeks 1-6, 6 to 8 steps each); the parser (0x15ee60) copies them to the
 * Paper Trail object (0xe17388, int32 at +8 ... +0xc4), sets +0x95c and, when one changed, calls
 * 0x571a00(0x7f79610) (rewards: it reads the steps back through 0x15ee10) and, in a mission
 * (0x7f77a80 +0x80 >= 2), 0x52e0a0(0x7f77a80). It only asks with PSN signed in, which the port
 * never is. With ISSN_PAPER_TRAIL=1 two script calls of that object are replaced:
 *   - 0x15e500 "update" (asks the server): marks the 48 steps done as the parser would;
 *   - 0x15ecc0 "status": the same, then answers 5 ("answer received").
 * Both jump to issn_paper_trail_answer (runtime_paper_trail.c) through movabs/jmp (rax is free at
 * a function's entry); it returns to the game's caller. */
#ifndef ISSN_PAPER_TRAIL_HOOK_H
#define ISSN_PAPER_TRAIL_HOOK_H
#include <stdint.h>
#include <string.h>

#define ISSN_PAPER_TRAIL_UPDATE 0x15e500u
#define ISSN_PAPER_TRAIL_STATUS 0x15ecc0u
#define ISSN_PAPER_TRAIL_URL 0x99727au
#define ISSN_PAPER_TRAIL_OBJECT 0xe17388u
#define ISSN_PAPER_TRAIL_STEPS 48u
#define ISSN_PAPER_TRAIL_STEP_AT 8u        /* int32 per step */
#define ISSN_PAPER_TRAIL_ANSWERED 0x95cu   /* byte: an answer was parsed */
#define ISSN_PAPER_TRAIL_REFRESH 0x571a00u /* (0x7f79610): rewards from the steps */
#define ISSN_PAPER_TRAIL_REFRESH_ARG 0x7f79610u
#define ISSN_PAPER_TRAIL_SAVE 0x52e0a0u    /* (0x7f77a80) when +0x80 >= 2 */
#define ISSN_PAPER_TRAIL_SAVE_ARG 0x7f77a80u
#define ISSN_PAPER_TRAIL_HOOK_LENGTH 12u
/* issn 11-12 (diagnostic): the script's four Paper Trail calls are 12-byte wrappers
 * "lea rdi, [rip + object]; jmp <function>" at 0xef610 (update), 0xef620 (status), 0xef630 (reset)
 * and 0xef640 (online id: the player's PSN id as a script string, empty without PSN). The last two
 * are wrapped to log the calls and then run the game's own function unchanged. */
#define ISSN_PAPER_TRAIL_WRAP_RESET 0xef630u
#define ISSN_PAPER_TRAIL_WRAP_ID 0xef640u
#define ISSN_PAPER_TRAIL_RESET 0x15ea40u
#define ISSN_PAPER_TRAIL_ID 0x15ed40u

/* push rbp ... cmp dword [r12+0x954],-1; jne; cmp dword [r12+0xd0],-1; je; lea rdi,[r12+0xc8];
 * call 0x236520; mov dword [r12+0x950],0; mov byte [r12+0x95c],0 */
static const unsigned char issn_paper_trail_update_code[] = {
    0x55, 0x48, 0x89, 0xe5, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x53, 0x48, 0x83, 0xec,
    0x58, 0x48, 0x8b, 0x1d, 0xe8, 0xf8, 0xc3, 0x00, 0x49, 0x89, 0xfc, 0x48, 0x8b, 0x03, 0x48, 0x89,
    0x45, 0xd0, 0x41, 0x83, 0xbc, 0x24, 0x54, 0x09, 0x00, 0x00, 0xff, 0x0f, 0x85, 0xee, 0x04, 0x00,
    0x00, 0x41, 0x83, 0xbc, 0x24, 0xd0, 0x00, 0x00, 0x00, 0xff, 0x74, 0x22, 0x49, 0x8d, 0xbc, 0x24,
    0xc8, 0x00, 0x00, 0x00, 0xe8, 0xd7, 0x7f, 0x0d, 0x00, 0x41, 0xc7, 0x84, 0x24, 0x50, 0x09, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x41, 0xc6, 0x84, 0x24, 0x5c, 0x09, 0x00, 0x00, 0x00};
/* mov ecx,[rdi+0x954]; cmp ecx,-1; jne; mov edx,[rdi+0xd0]; mov eax,3; lea esi,[rdx-3]; cmp esi,2;
 * jb; cmp edx,-1; je; cmp edx,2; jne; cmp byte [rdi+0x95c],0 */
static const unsigned char issn_paper_trail_status_code[] = {
    0x8b, 0x8f, 0x54, 0x09, 0x00, 0x00, 0x83, 0xf9, 0xff, 0x75, 0x31, 0x8b, 0x97, 0xd0, 0x00, 0x00,
    0x00, 0xb8, 0x03, 0x00, 0x00, 0x00, 0x8d, 0x72, 0xfd, 0x83, 0xfe, 0x02, 0x72, 0x57, 0x83, 0xfa,
    0xff, 0x74, 0x19, 0x83, 0xfa, 0x02, 0x75, 0x4e, 0x80, 0xbf, 0x5c, 0x09, 0x00, 0x00, 0x00};
static const char issn_paper_trail_url[] = "https://api.infamouspapertrail.com/update";

/* The wrapper at `at` is "lea rdi, [rip + object]; jmp target" (rel32s checked). */
static inline int issn_paper_trail_wrapper(const unsigned char *image, uint32_t at, uint32_t target) {
    int32_t lea, jmp;
    if (image[at] != 0x48 || image[at + 1] != 0x8d || image[at + 2] != 0x3d || image[at + 7] != 0xe9) return 0;
    memcpy(&lea, image + at + 3, 4);
    memcpy(&jmp, image + at + 8, 4);
    return at + 7 + lea == ISSN_PAPER_TRAIL_OBJECT && at + 12 + jmp == target;
}

static inline int issn_paper_trail_match(const unsigned char *image, uint64_t size) {
    if (size < ISSN_PAPER_TRAIL_URL + sizeof(issn_paper_trail_url) ||
        size < ISSN_PAPER_TRAIL_STATUS + sizeof(issn_paper_trail_status_code)) return 0;
    return !memcmp(image + ISSN_PAPER_TRAIL_UPDATE, issn_paper_trail_update_code, sizeof(issn_paper_trail_update_code)) &&
           !memcmp(image + ISSN_PAPER_TRAIL_STATUS, issn_paper_trail_status_code, sizeof(issn_paper_trail_status_code)) &&
           !memcmp(image + ISSN_PAPER_TRAIL_URL, issn_paper_trail_url, sizeof(issn_paper_trail_url));
}

/* movabs $target,%rax; jmp *%rax (12 bytes). */
static inline void issn_paper_trail_jump(unsigned char *out, const void *target) {
    const uint64_t to = (uint64_t)(uintptr_t)target;
    out[0] = 0x48; out[1] = 0xb8; memcpy(out + 2, &to, 8);
    out[10] = 0xff; out[11] = 0xe0;
}
#endif
