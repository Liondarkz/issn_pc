/* SPDX-License-Identifier: GPL-2.0-or-later */
/* issn 11-05: Paper Trail without its website (src/paper_trail_hook.h). The game's "update" and
 * "status" calls of the Paper Trail object land here: the 48 website steps are marked done the
 * way the game's own parser does it, and the status is "answer received". */
#ifndef ABI
#define ABI __attribute__((sysv_abi))
#endif
#include "paper_trail_hook.h"
#include <stdio.h>

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

ABI int issn_paper_trail_answer(unsigned char *object) {
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
    return 5;
}
