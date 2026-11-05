/* Trophies kept on this PC (src/runtime_trophy.c): the game's trophy file (a synthetic one here:
 * three icons and an encrypted-looking entry), the unlocked list and the platinum. */
#undef NDEBUG /* the checks are asserts; release builds define NDEBUG */
#include "../src/runtime_trophy.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#ifdef _WIN32
#include <direct.h>
#define make_dir(path) _mkdir(path)
#else
#define make_dir(path) mkdir(path, 0755)
#endif

static void put32(unsigned char *p, uint32_t v) { p[0]=v>>24; p[1]=v>>16; p[2]=v>>8; p[3]=v; }
static void put64(unsigned char *p, uint64_t v) { put32(p,(uint32_t)(v>>32)); put32(p+4,(uint32_t)v); }

/* A .trp as the PS4 lays it out: big-endian header, 64-byte entries, data after them. */
static void write_trp(const char *path) {
    static const char *names[]={"ICON0.PNG","TROP000.PNG","TROP001.PNG","TROP002.PNG","TROPCONF.ESFM"};
    enum { COUNT=5, DATA=0x60+COUNT*64, LEN=24 };
    unsigned char file[DATA+COUNT*LEN]; memset(file,0,sizeof(file));
    put32(file,0xDCA24D00u); put32(file+4,3); put64(file+8,sizeof(file)); put32(file+16,COUNT); put32(file+20,64);
    for (int i=0;i<COUNT;++i) {
        unsigned char *e=file+0x60+i*64, *d=file+DATA+i*LEN;
        strcpy((char *)e,names[i]);
        put64(e+32,DATA+i*LEN); put64(e+40,LEN); put32(e+48,i==COUNT-1 ? 3 : 0);
        if (i<COUNT-1) { memcpy(d,"\x89PNG\r\n\x1a\n",8); d[8]=(unsigned char)i; } else memset(d,0xa5,LEN);
    }
    FILE *f=fopen(path,"wb"); assert(f); fwrite(file,1,sizeof(file),f); fclose(f);
}

int main(void) {
    make_dir("out"); make_dir("out/trophy-data");
    const char *trp="out/trophy-data/trophy00.trp", *list="out/trophy-data/trophies.txt",
               *names="out/trophy-data/trophy-names.txt";
    write_trp(trp);
    remove(list);

    /* The file: three trophies (ids 0..2); icons come out as PNG bytes; others are not icons. */
    assert(trophy_trp_count(trp)==3);
    assert(trophy_trp_count("out/trophy-data/missing.trp")==-1 && trophy_trp_count(list)==-1);
    size_t size=0; unsigned char *png=trophy_trp_icon(trp,2,&size);
    assert(png && size==24 && !memcmp(png,"\x89PNG",4) && png[8]==3); /* entry 3 is TROP002 */
    free(png);
    assert(!trophy_trp_icon(trp,3,&size) && size==0);

    /* Without names: the platinum is id 0 and needs 1 and 2. */
    TrophyBook book; int platinum=5;
    trophy_book_init(&book,3);
    assert(book.platinum==0 && book.base[1] && book.base[2] && !book.base[0]);
    assert(trophy_book_unlock(&book,0,"2026-11-04 10:00:00",&platinum)==-2 && platinum==-1);
    assert(trophy_book_unlock(&book,3,"x",&platinum)==-1);
    assert(trophy_book_unlock(&book,1,"2026-11-04 10:00:00",&platinum)==0 && platinum==-1);
    assert(trophy_book_unlock(&book,1,"2026-11-04 10:00:01",&platinum)==1);
    assert(trophy_book_platinum_due(&book)==-1);
    assert(trophy_book_unlock(&book,2,"2026-11-04 10:05:00",&platinum)==0 && platinum==0);
    assert(trophy_book_unlocked(&book,0) && trophy_book_count(&book)==3);
    assert(!strcmp(book.when[0],"2026-11-04 10:05:00") && !strcmp(book.when[1],"2026-11-04 10:00:00"));

    /* Kept in user/trophies.txt and read back with the dates. */
    assert(trophy_book_save(&book,list)==0);
    TrophyBook again; trophy_book_init(&again,3);
    assert(trophy_book_load(&again,list)==3 && !strcmp(again.when[2],"2026-11-04 10:05:00"));
    assert(!memcmp(again.flags,book.flags,sizeof(book.flags)));
    assert(trophy_book_save(&again,list)==0); /* replaces the file */

    /* A file from an older run with a platinum due: it is given at start (not by the game). */
    FILE *f=fopen(list,"w"); fputs("# comment\n1 2026-01-01 00:00:00\n2 2026-01-02 00:00:00\nnot a line\n9 x\n",f); fclose(f);
    trophy_book_init(&again,3);
    assert(trophy_book_load(&again,list)==2 && trophy_book_platinum_due(&again)==0);

    /* Names from the player's key: the list decides which trophy is the platinum (here 2) and
     * which ones count toward it (1 only). */
    f=fopen(names,"w");
    fputs("# id\tgrade\tbase\tname\n0\tG\t1\tFirst\n1\tB\t1\tSecond, with ção\n2\tP\t0\tAll of them\n",f);
    fclose(f);
    trophy_book_init(&again,3);
    assert(trophy_book_names(&again,names)==3 && again.platinum==2 && again.grade[1]=='B');
    assert(!strcmp(again.name[1],"Second, with ção") && !again.base[2] && again.base[0] && again.base[1]);
    assert(trophy_book_unlock(&again,0,"a",&platinum)==0 && platinum==-1);
    assert(trophy_book_unlock(&again,1,"b",&platinum)==0 && platinum==2);
    assert(trophy_book_unlock(&again,2,"c",&platinum)==1);

    /* No trophy file: any id the console allows is kept, nothing is a platinum. */
    trophy_book_init(&again,-1);
    assert(again.total==0 && again.platinum==-1);
    assert(trophy_book_unlock(&again,47,"d",&platinum)==0 && platinum==-1 && trophy_book_unlock(&again,128,"d",&platinum)==-1);
    puts("trophy test: ok");
    return 0;
}
