/* issn: the game's trophies, kept on this PC (runtime_trophy.c).
 *   - user/trophies.txt: the trophies unlocked here, one "id date" line each (the launcher's
 *     Achievements page reads it too);
 *   - the game's sce_sys/trophy/trophy00.trp (ISSN_TROPHY_FILE, found by run.py): its icons are
 *     plain PNG files and their count is the number of trophies; its texts are encrypted;
 *   - user/trophy-names.txt: names and grades, written by run.py only when the player gave the
 *     launcher their own trophy key (never shipped). Without it a trophy is "Trophy N". */
#ifndef ISSN_RUNTIME_TROPHY_H
#define ISSN_RUNTIME_TROPHY_H
#include <stddef.h>
#include <stdint.h>

#define TROPHY_MAX 128

typedef struct {
    uint32_t flags[TROPHY_MAX / 32];   /* bit id%32 of word id/32: unlocked (sceNpTrophy layout) */
    char when[TROPHY_MAX][20];         /* local time of the unlock, "YYYY-MM-DD HH:MM:SS" */
    int total;                         /* trophies in the game's file; 0 = unknown */
    int platinum;                      /* the platinum's id; -1 = none or unknown */
    unsigned char base[TROPHY_MAX];    /* 1: needed for the platinum */
    char grade[TROPHY_MAX];            /* 'P', 'G', 'S', 'B'; 0 = unknown */
    char name[TROPHY_MAX][128];        /* UTF-8; "" = unknown */
} TrophyBook;

/* Number of trophies in a .trp file (TROPnnn.PNG entries, ids 0..n-1); -1 when the file is
 * missing or not a trophy file. */
int trophy_trp_count(const char *path);
/* The PNG bytes of trophy `id`'s icon (malloc'd, the caller frees) or NULL. */
unsigned char *trophy_trp_icon(const char *path, int id, size_t *size);

/* An empty book for `total` trophies: without names, the platinum is id 0 (the trophy file
 * format puts it first) and every other trophy counts toward it. */
void trophy_book_init(TrophyBook *book, int total);
/* Adds the unlocked trophies of user/trophies.txt; returns how many were read (0: no file). */
int trophy_book_load(TrophyBook *book, const char *path);
/* Adds names and grades from user/trophy-names.txt ("id\tgrade\tbase\tname" lines); returns how
 * many were read. A file that names a platinum also decides which trophy that is. */
int trophy_book_names(TrophyBook *book, const char *path);
/* Writes user/trophies.txt whole (a new file then a rename); 0 on success. */
int trophy_book_save(const TrophyBook *book, const char *path);
int trophy_book_unlocked(const TrophyBook *book, int id);
int trophy_book_count(const TrophyBook *book);
/* Marks `id` unlocked at `when`. Returns 0 (new), 1 (was unlocked), -1 (not a trophy of this
 * game), -2 (the platinum: only the system unlocks it). When this was the last trophy the
 * platinum needs, the platinum is unlocked too and *platinum gets its id (else -1). */
int trophy_book_unlock(TrophyBook *book, int id, const char *when, int *platinum);
/* The platinum's id when every trophy it needs is unlocked and it is not yet; else -1. */
int trophy_book_platinum_due(const TrophyBook *book);
#endif
