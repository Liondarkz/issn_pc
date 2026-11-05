/* issn: the game's trophies, kept on this PC (see runtime_trophy.h). Plain C, no guest state:
 * runtime_services.c calls it from the sceNpTrophy functions. */
#define _GNU_SOURCE
#include "runtime_trophy.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#endif

/* ---- the game's trophy file (big-endian: header 0x60 bytes, then entries) ---- */
#define TRP_MAGIC 0xDCA24D00u
#define TRP_HEADER 0x60

static uint32_t be32(const unsigned char *p) { return (uint32_t)p[0]<<24 | (uint32_t)p[1]<<16 | (uint32_t)p[2]<<8 | p[3]; }
static uint64_t be64(const unsigned char *p) { return (uint64_t)be32(p)<<32 | be32(p+4); }

/* The id of an entry named TROPnnn.PNG, else -1. */
static int icon_id(const char *name) {
    if (strncmp(name,"TROP",4) || strcmp(name+7,".PNG")) return -1;
    int id=0;
    for (int i=4;i<7;++i) {
        if (name[i]<'0' || name[i]>'9') return -1;
        id=id*10+(name[i]-'0');
    }
    return id;
}

/* Calls `found` for every icon entry; returns the number of entries or -1. */
static int trp_walk(FILE *f,int (*found)(int id,uint64_t pos,uint64_t len,void *ctx),void *ctx) {
    unsigned char head[TRP_HEADER];
    if (fread(head,1,sizeof(head),f)!=sizeof(head) || be32(head)!=TRP_MAGIC) return -1;
    uint32_t count=be32(head+16), size=be32(head+20);
    if (count>4096 || size<52 || size>1024) return -1;
    unsigned char entry[1024];
    for (uint32_t i=0;i<count;++i) {
        if (fseek(f,(long)(TRP_HEADER+(uint64_t)i*size),SEEK_SET) || fread(entry,1,size,f)!=size) return -1;
        char name[33]; memcpy(name,entry,32); name[32]=0;
        int id=icon_id(name);
        if (id>=0 && found(id,be64(entry+32),be64(entry+40),ctx)) break;
    }
    return (int)count;
}

static int count_icon(int id,uint64_t pos,uint64_t len,void *ctx) {
    (void)pos; (void)len;
    int *highest=ctx;
    if (id<TROPHY_MAX && id+1>*highest) *highest=id+1;
    return 0;
}

int trophy_trp_count(const char *path) {
    FILE *f=path && *path ? fopen(path,"rb") : NULL;
    if (!f) return -1;
    int total=0, entries=trp_walk(f,count_icon,&total);
    fclose(f);
    return entries<0 ? -1 : total;
}

typedef struct { int id; uint64_t pos, len; int hit; } IconFind;
static int find_icon(int id,uint64_t pos,uint64_t len,void *ctx) {
    IconFind *want=ctx;
    if (id!=want->id) return 0;
    want->pos=pos; want->len=len; want->hit=1;
    return 1;
}

unsigned char *trophy_trp_icon(const char *path,int id,size_t *size) {
    if (size) *size=0;
    FILE *f=path && *path ? fopen(path,"rb") : NULL;
    if (!f) return NULL;
    IconFind want={id,0,0,0};
    unsigned char *png=NULL;
    if (trp_walk(f,find_icon,&want)>=0 && want.hit && want.len>8 && want.len<(16u<<20)
        && !fseek(f,(long)want.pos,SEEK_SET) && (png=malloc(want.len))) {
        if (fread(png,1,want.len,f)!=want.len || memcmp(png,"\x89PNG",4)) { free(png); png=NULL; }
        else if (size) *size=(size_t)want.len;
    }
    fclose(f);
    return png;
}

/* ---- the book of unlocked trophies ---- */
static int valid(const TrophyBook *book,int id) {
    return id>=0 && id<TROPHY_MAX && (book->total<=0 || id<book->total);
}

void trophy_book_init(TrophyBook *book,int total) {
    memset(book,0,sizeof(*book));
    book->total=total>0 && total<=TROPHY_MAX ? total : 0;
    book->platinum=book->total>1 ? 0 : -1;
    for (int i=1;i<book->total;++i) book->base[i]=1;
    if (book->platinum==0) book->grade[0]='P';
}

int trophy_book_unlocked(const TrophyBook *book,int id) {
    return id>=0 && id<TROPHY_MAX && (book->flags[id>>5]>>(id&31)&1);
}

int trophy_book_count(const TrophyBook *book) {
    int n=0;
    for (int i=0;i<TROPHY_MAX;++i) n+=trophy_book_unlocked(book,i);
    return n;
}

static void mark(TrophyBook *book,int id,const char *when) {
    book->flags[id>>5]|=1u<<(id&31);
    snprintf(book->when[id],sizeof(book->when[id]),"%s",when ? when : "");
}

int trophy_book_load(TrophyBook *book,const char *path) {
    FILE *f=path ? fopen(path,"r") : NULL;
    if (!f) return 0;
    char line[256]; int read=0;
    while (fgets(line,sizeof(line),f)) {
        char *end; long id=strtol(line,&end,10);
        if (end==line || line[0]=='#' || !valid(book,(int)id)) continue;
        while (*end==' ' || *end=='\t') ++end;
        end[strcspn(end,"\r\n")]=0;
        if (!trophy_book_unlocked(book,(int)id)) { mark(book,(int)id,end); ++read; }
    }
    fclose(f);
    return read;
}

int trophy_book_names(TrophyBook *book,const char *path) {
    FILE *f=path ? fopen(path,"r") : NULL;
    if (!f) return 0;
    char line[2048]; int read=0, platinum=-1, graded=0;
    unsigned char base[TROPHY_MAX]={0};
    while (fgets(line,sizeof(line),f)) {
        line[strcspn(line,"\r\n")]=0;
        char *field[4]={line,NULL,NULL,NULL};
        for (int i=1;i<4;++i) {
            char *tab=field[i-1] ? strchr(field[i-1],'\t') : NULL;
            if (!tab) break;
            *tab=0; field[i]=tab+1;
        }
        char *end; long id=strtol(field[0],&end,10);
        if (line[0]=='#' || end==field[0] || *end || !field[3] || !valid(book,(int)id)) continue;
        char grade=field[1][0];
        if (grade=='P' || grade=='G' || grade=='S' || grade=='B') {
            book->grade[id]=grade; graded=1;
            if (grade=='P') platinum=(int)id;
        }
        base[id]=field[2][0]=='1';
        snprintf(book->name[id],sizeof(book->name[id]),"%s",field[3]);
        ++read;
    }
    fclose(f);
    if (graded) {  /* the game's own list (TROPCONF) decides the platinum and what it needs */
        book->platinum=platinum;
        memcpy(book->base,base,sizeof(base));
        if (platinum>=0) book->base[platinum]=0;
    }
    return read;
}

int trophy_book_save(const TrophyBook *book,const char *path) {
    char part[1024];
    if (!path || snprintf(part,sizeof(part),"%s.part",path)>=(int)sizeof(part)) return -1;
    FILE *f=fopen(part,"w");
    if (!f) return -1;
    fprintf(f,"# Trophies unlocked in this port: id and local date. Removing a line locks it again.\n");
    for (int i=0;i<TROPHY_MAX;++i)
        if (trophy_book_unlocked(book,i)) fprintf(f,"%d %s\n",i,book->when[i]);
    int bad=ferror(f);
    if (fclose(f) || bad) { remove(part); return -1; }
#ifdef _WIN32
    if (!MoveFileExA(part,path,MOVEFILE_REPLACE_EXISTING)) { remove(part); return -1; }
#else
    if (rename(part,path)) { remove(part); return -1; }
#endif
    return 0;
}

int trophy_book_platinum_due(const TrophyBook *book) {
    int p=book->platinum, needed=0;
    if (p<0 || trophy_book_unlocked(book,p)) return -1;
    for (int i=0;i<TROPHY_MAX;++i) {
        if (!book->base[i] || i==p) continue;
        ++needed;
        if (!trophy_book_unlocked(book,i)) return -1;
    }
    return needed ? p : -1;
}

int trophy_book_unlock(TrophyBook *book,int id,const char *when,int *platinum) {
    if (platinum) *platinum=-1;
    if (!valid(book,id)) return -1;
    if (trophy_book_unlocked(book,id)) return 1;
    if (id==book->platinum) return -2;
    mark(book,id,when);
    int p=trophy_book_platinum_due(book);
    if (p>=0) {
        mark(book,p,when);
        if (platinum) *platinum=p;
    }
    return 0;
}
