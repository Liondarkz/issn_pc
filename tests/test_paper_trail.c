/* Paper Trail without its website (src/paper_trail_hook.h, src/runtime_paper_trail.c): the 48
 * steps are marked as the game's parser does, the hook is a movabs/jmp, and the sites must match
 * the 01.07 code (a made-up image here: the game's code is not in the repository). */
#undef NDEBUG
#include "../src/paper_trail_hook.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

int issn_paper_trail_mark(unsigned char *object);

int main(void) {
    unsigned char object[0x960];
    memset(object, 0xff, sizeof(object));
    int32_t one = 1;
    memcpy(object + ISSN_PAPER_TRAIL_STEP_AT + 4 * 7, &one, 4);
    assert(issn_paper_trail_mark(object) == 1);
    for (unsigned i = 0; i < ISSN_PAPER_TRAIL_STEPS; ++i) {
        int32_t step;
        memcpy(&step, object + ISSN_PAPER_TRAIL_STEP_AT + 4 * i, 4);
        assert(step == 1);
    }
    assert(object[ISSN_PAPER_TRAIL_ANSWERED] == 1);
    assert(object[ISSN_PAPER_TRAIL_STEP_AT - 1] == 0xff && object[ISSN_PAPER_TRAIL_STEP_AT + 4 * 48] == 0xff);
    assert(issn_paper_trail_mark(object) == 0); /* nothing changes the second time: no reward call */

    const uint64_t size = ISSN_PAPER_TRAIL_URL + 0x100;
    unsigned char *image = calloc(1, size);
    assert(image);
    assert(!issn_paper_trail_match(image, size));
    memcpy(image + ISSN_PAPER_TRAIL_UPDATE, issn_paper_trail_update_code, sizeof(issn_paper_trail_update_code));
    memcpy(image + ISSN_PAPER_TRAIL_STATUS, issn_paper_trail_status_code, sizeof(issn_paper_trail_status_code));
    assert(!issn_paper_trail_match(image, size)); /* no URL: another build */
    memcpy(image + ISSN_PAPER_TRAIL_URL, issn_paper_trail_url, sizeof(issn_paper_trail_url));
    assert(issn_paper_trail_match(image, size));
    assert(!issn_paper_trail_match(image, ISSN_PAPER_TRAIL_URL + 8));
    image[ISSN_PAPER_TRAIL_STATUS + 2] ^= 1;
    assert(!issn_paper_trail_match(image, size));

    unsigned char jump[ISSN_PAPER_TRAIL_HOOK_LENGTH];
    issn_paper_trail_jump(jump, (const void *)(uintptr_t)0x1122334455667788ull);
    const unsigned char want[] = {0x48, 0xb8, 0x88, 0x77, 0x66, 0x55, 0x44, 0x33, 0x22, 0x11, 0xff, 0xe0};
    assert(!memcmp(jump, want, sizeof(want)));
    free(image);
    puts("paper trail test: ok");
    return 0;
}
