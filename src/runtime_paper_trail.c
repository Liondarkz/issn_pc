/* SPDX-License-Identifier: GPL-2.0-or-later */
/* issn 11-05: Paper Trail without its website (src/paper_trail_hook.h). The game's "update" and
 * "status" calls of the Paper Trail object land here: the 48 website steps are marked done the
 * way the game's own parser does it, and the status is "answer received". */
#ifndef ABI
#define ABI __attribute__((sysv_abi))
#endif
#include "paper_trail_hook.h"
#include <stdio.h>
#include <stdlib.h>

static unsigned char *paper_trail_image;

void issn_paper_trail_attach(unsigned char *image) { paper_trail_image = image; }

/* Marks every step done; returns 1 when one changed. Separate from the game calls for tests. */
int issn_paper_trail_mark(unsigned char *object) {
    int changed = 0;
    for (unsigned i = 0; i < ISSN_PAPER_TRAIL_STEPS; ++i) {
        int32_t step;
        memcpy(&step, object + ISSN_PAPER_TRAIL_STEP_AT + 4 * i, 4);
        if (step != 1) {
            step = 1;
            memcpy(object + ISSN_PAPER_TRAIL_STEP_AT + 4 * i, &step, 4);
            changed = 1;
        }
    }
    object[ISSN_PAPER_TRAIL_ANSWERED] = 1;
    return changed;
}

typedef ABI void (*PaperTrailCall)(void *);
typedef ABI void *(*PaperTrailPass)(void *, void *);

/* issn 11-12 (diagnostic): how the game's script uses Paper Trail. The first calls of each kind
 * are logged with the script code that made them (offset in the eboot), then every 256th. */
static unsigned paper_trail_calls[4];
static void paper_trail_note(int kind, const void *caller, long answer) {
    static const char *names[4] = {"update", "status", "reset", "online id"};
    unsigned n = ++paper_trail_calls[kind];
    if (n > 6 && (n & 255)) return;
    long from = paper_trail_image ? (long)((const unsigned char *)caller - paper_trail_image) : 0;
    if (answer == -1000)
        printf("Paper Trail call: %s #%u from 0x%lx\n", names[kind], n, from);
    else
        printf("Paper Trail call: %s #%u from 0x%lx -> %ld\n", names[kind], n, from, answer);
    fflush(stdout);
}

/* ISSN_PAPER_TRAIL_STATUS=0..5 (test): always answer that status. The game's own values:
 * 0 no PSN, 1 connected and idle, 2 connecting, 3 request on its way, 4 an answer it could not
 * use, 5 an answer read. -1: answer as the game would (see issn_paper_trail_status). */
static int paper_trail_status_value(void) {
    static int value = -2;
    if (value == -2) {
        const char *text = getenv("ISSN_PAPER_TRAIL_STATUS");
        value = text && text[0] >= '0' && text[0] <= '5' && !text[1] ? text[0] - '0' : -1;
    }
    return value;
}

static void paper_trail_answer(unsigned char *object) {
    static int told;
    if (issn_paper_trail_mark(object) && paper_trail_image) {
        unsigned char *image = paper_trail_image;
        ((PaperTrailCall)(image + ISSN_PAPER_TRAIL_REFRESH))(image + ISSN_PAPER_TRAIL_REFRESH_ARG);
        int32_t mission;
        memcpy(&mission, image + ISSN_PAPER_TRAIL_SAVE_ARG + 0x80, 4);
        if (mission >= 2) ((PaperTrailCall)(image + ISSN_PAPER_TRAIL_SAVE))(image + ISSN_PAPER_TRAIL_SAVE_ARG);
        if (told++ < 4) {
            printf("Paper Trail: the website's 48 steps marked done (no internet needed)\n");
            fflush(stdout);
        }
    }
}

/* "update" (the script asks the website): the answer arrives at once, as the parser would leave it. */
ABI int issn_paper_trail_update(unsigned char *object) {
    paper_trail_answer(object);
    paper_trail_note(0, __builtin_return_address(0), -1000);
    return 1;
}

/* "status", as the game's own code answers with the network up (0x15ecc0): 5 while an answer is
 * kept (+0x95c), else 1 "connected, idle". issn 11-12: the script resets a kept answer when it
 * sees one before asking (João's log: status 5, 5, 5, reset, 5 ...) and only calls "update" once
 * the status is 1, so a constant 5 left the prompt at "Checking status..." for ever. */
int issn_paper_trail_status_of(const unsigned char *object) {
    int value = paper_trail_status_value();
    return value >= 0 ? value : object[ISSN_PAPER_TRAIL_ANSWERED] ? 5 : 1;
}

ABI int issn_paper_trail_status(unsigned char *object) {
    int value = issn_paper_trail_status_of(object);
    paper_trail_note(1, __builtin_return_address(0), value);
    return value;
}

/* Kept for the tests and older callers: the status answer. */
ABI int issn_paper_trail_answer(unsigned char *object) { return issn_paper_trail_status(object); }

ABI void issn_paper_trail_reset(void) {
    paper_trail_note(2, __builtin_return_address(0), -1000);
    unsigned char *image = paper_trail_image;
    ((PaperTrailCall)(image + ISSN_PAPER_TRAIL_RESET))(image + ISSN_PAPER_TRAIL_OBJECT);
}

ABI void *issn_paper_trail_online_id(void *unused, void *context) {
    (void)unused;
    paper_trail_note(3, __builtin_return_address(0), -1000);
    unsigned char *image = paper_trail_image;
    return ((PaperTrailPass)(image + ISSN_PAPER_TRAIL_ID))(image + ISSN_PAPER_TRAIL_OBJECT, context);
}
