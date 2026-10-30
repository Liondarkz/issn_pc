/* issn: kernel event flags (inFAMOUS Second Son): a 64-bit pattern that threads wait on until
 * some bits (OR) or all of them (AND) are set, optionally clearing them when the wait ends.
 *
 * The handle the game keeps (SceKernelEventFlag, pointer sized) is an id here. A flag created
 * with attribute SINGLE (0x10, the default) takes one waiter at a time (a second one gets
 * EPERM); MULTI (0x20) takes any number. Waiters are woken together and test the pattern again,
 * in arrival order. */
#define _GNU_SOURCE
#include "runtime.h"
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define ERR(n) ((int32_t)(UINT32_C(0x80020000)|(n)))
enum { ATTR_SINGLE=0x10, ATTR_MULTI=0x20 };
enum { WAIT_AND=0x01, WAIT_OR=0x02, WAIT_CLEAR_ALL=0x10, WAIT_CLEAR_PAT=0x20 };

typedef struct Flag {
    uint64_t id, pattern;
    uint32_t attr;
    unsigned waiters;
    uint64_t cancels;   /* each cancel or the deletion ends the waits that started before it */
    int deleted;
    pthread_cond_t changed;
    char name[32];
    struct Flag *next;
} Flag;

static pthread_mutex_t lock=PTHREAD_MUTEX_INITIALIZER;
static Flag *flags;
static uint64_t next_id=1;
static size_t created, waits, timeouts;

/* As runtime_sema.c: winpthreads and macOS condition variables accept only CLOCK_REALTIME. */
#if defined(_WIN32) || defined(__APPLE__)
#define WAIT_CLOCK CLOCK_REALTIME
#else
#define WAIT_CLOCK CLOCK_MONOTONIC
#endif
static uint64_t now_ns(void) {
    struct timespec t;
    clock_gettime(WAIT_CLOCK,&t);
    return (uint64_t)t.tv_sec*1000000000u+(uint64_t)t.tv_nsec;
}
static void host_check(int e) {
    if (e) { fprintf(stderr,"STOP: event flag host error %d\n",e); exit(21); }
}
static Flag *find(uint64_t id) {
    for (Flag *f=flags;f;f=f->next) if (f->id==id && !f->deleted) return f;
    return NULL;
}
static int valid_mode(uint32_t mode) {
    const uint32_t wait=mode & (WAIT_AND|WAIT_OR), clear=mode & (WAIT_CLEAR_ALL|WAIT_CLEAR_PAT);
    return (wait==WAIT_AND || wait==WAIT_OR) && clear!=(WAIT_CLEAR_ALL|WAIT_CLEAR_PAT) &&
           !(mode & ~(uint32_t)(WAIT_AND|WAIT_OR|WAIT_CLEAR_ALL|WAIT_CLEAR_PAT));
}
static int satisfied(uint64_t pattern, uint64_t bits, uint32_t mode) {
    return (mode & WAIT_AND) ? (pattern & bits)==bits : (pattern & bits)!=0;
}
/* The pattern before the clear goes to *result; then the mode's clear. */
static void take(Flag *f, uint64_t bits, uint32_t mode, uint64_t *result) {
    if (result) *result=f->pattern;
    if (mode & WAIT_CLEAR_ALL) f->pattern=0;
    else if (mode & WAIT_CLEAR_PAT) f->pattern&=~bits;
}

static ABI int32_t flag_create(uint64_t *handle, const char *name, uint32_t attr, uint64_t pattern,
                               const void *options) {
    (void)options;
    if (!handle || !name) return ERR(22);
    if (attr & ~(uint32_t)(0x03|ATTR_SINGLE|ATTR_MULTI) || (attr & ATTR_SINGLE && attr & ATTR_MULTI)) return ERR(22);
    Flag *f=calloc(1,sizeof(*f));
    if (!f) return ERR(12);
    pthread_condattr_t ca;
    host_check(pthread_condattr_init(&ca));
    host_check(pthread_condattr_setclock(&ca,WAIT_CLOCK));
    host_check(pthread_cond_init(&f->changed,&ca));
    host_check(pthread_condattr_destroy(&ca));
    snprintf(f->name,sizeof(f->name),"%s",name);
    f->attr=attr & ATTR_MULTI ? ATTR_MULTI : ATTR_SINGLE;
    f->pattern=pattern;
    pthread_mutex_lock(&lock);
    f->id=next_id++;
    f->next=flags; flags=f; ++created;
    *handle=f->id;
    pthread_mutex_unlock(&lock);
    printf("Runtime: event flag %llu '%s' attr=0x%x pattern=0x%llx\n",(unsigned long long)f->id,f->name,attr,
           (unsigned long long)pattern);
    return 0;
}

static ABI int32_t flag_delete(uint64_t handle) {
    pthread_mutex_lock(&lock);
    Flag *f=find(handle);
    if (!f) { pthread_mutex_unlock(&lock); return ERR(3); }
    f->deleted=1; ++f->cancels;
    host_check(pthread_cond_broadcast(&f->changed));
    /* Kept in the list (deleted) while waiters leave; freed by the last one, or now. */
    if (!f->waiters) {
        Flag **p=&flags;
        while (*p!=f) p=&(*p)->next;
        *p=f->next;
        host_check(pthread_cond_destroy(&f->changed));
        free(f);
    }
    pthread_mutex_unlock(&lock);
    return 0;
}

static ABI int32_t flag_set(uint64_t handle, uint64_t bits) {
    pthread_mutex_lock(&lock);
    Flag *f=find(handle);
    if (!f) { pthread_mutex_unlock(&lock); return ERR(3); }
    f->pattern|=bits;
    host_check(pthread_cond_broadcast(&f->changed));
    pthread_mutex_unlock(&lock);
    return 0;
}

/* The PS4 clears by AND: the bits left are the pattern's bits that are also in `bits`. */
static ABI int32_t flag_clear(uint64_t handle, uint64_t bits) {
    pthread_mutex_lock(&lock);
    Flag *f=find(handle);
    if (!f) { pthread_mutex_unlock(&lock); return ERR(3); }
    f->pattern&=bits;
    pthread_mutex_unlock(&lock);
    return 0;
}

static ABI int32_t flag_poll(uint64_t handle, uint64_t bits, uint32_t mode, uint64_t *result) {
    if (!bits || !valid_mode(mode)) return ERR(22);
    pthread_mutex_lock(&lock);
    Flag *f=find(handle);
    int32_t r=0;
    if (!f) r=ERR(3);
    else if (f->attr==ATTR_SINGLE && f->waiters) r=ERR(1);
    else if (satisfied(f->pattern,bits,mode)) take(f,bits,mode,result);
    else { if (result) *result=f->pattern; r=ERR(16); }
    pthread_mutex_unlock(&lock);
    return r;
}

/* timeout: microseconds, NULL waits forever; on return it holds the time left. */
static ABI int32_t flag_wait(uint64_t handle, uint64_t bits, uint32_t mode, uint64_t *result, uint32_t *timeout) {
    if (!bits || !valid_mode(mode)) return ERR(22);
    pthread_mutex_lock(&lock);
    Flag *f=find(handle);
    if (!f) { pthread_mutex_unlock(&lock); return ERR(3); }
    if (f->attr==ATTR_SINGLE && f->waiters) { pthread_mutex_unlock(&lock); return ERR(1); }
    ++waits;
    const uint64_t deadline=timeout ? now_ns()+(uint64_t)*timeout*1000u : 0;
    const struct timespec end={.tv_sec=(time_t)(deadline/1000000000u),.tv_nsec=(long)(deadline%1000000000u)};
    const uint64_t cancels=f->cancels;
    int32_t r=0;
    ++f->waiters;
    while (!satisfied(f->pattern,bits,mode)) {
        if (f->cancels!=cancels) { r=f->deleted ? ERR(13) : ERR(85); break; }
        if (timeout && !*timeout) { r=ERR(60); break; }
        const int e=timeout ? pthread_cond_timedwait(&f->changed,&lock,&end) : pthread_cond_wait(&f->changed,&lock);
        if (f->cancels!=cancels) { r=f->deleted ? ERR(13) : ERR(85); break; }
        if (e==ETIMEDOUT) {
            if (satisfied(f->pattern,bits,mode)) break;
            r=ERR(60); ++timeouts; break;
        }
        host_check(e);
    }
    if (!r) take(f,bits,mode,result);
    else if (result && !f->deleted) *result=f->pattern;
    if (timeout) {
        const uint64_t now=now_ns();
        *timeout=r || now>=deadline ? 0 : (uint32_t)((deadline-now)/1000u);
    }
    if (!--f->waiters && f->deleted) {
        Flag **p=&flags;
        while (*p!=f) p=&(*p)->next;
        *p=f->next;
        host_check(pthread_cond_destroy(&f->changed));
        free(f);
    }
    pthread_mutex_unlock(&lock);
    return r;
}

/* Sets the pattern and ends every wait in progress with ECANCELED. */
static ABI int32_t flag_cancel(uint64_t handle, uint64_t pattern, int32_t *woken) {
    pthread_mutex_lock(&lock);
    Flag *f=find(handle);
    if (!f) { pthread_mutex_unlock(&lock); return ERR(3); }
    if (woken) *woken=(int32_t)f->waiters;
    f->pattern=pattern; ++f->cancels;
    host_check(pthread_cond_broadcast(&f->changed));
    pthread_mutex_unlock(&lock);
    return 0;
}

static const RuntimeExport exports[]={
    {"sceKernelCreateEventFlag",flag_create}, {"sceKernelDeleteEventFlag",flag_delete},
    {"sceKernelSetEventFlag",flag_set}, {"sceKernelClearEventFlag",flag_clear},
    {"sceKernelPollEventFlag",flag_poll}, {"sceKernelWaitEventFlag",flag_wait},
    {"sceKernelCancelEventFlag",flag_cancel},
};
uintptr_t runtime_eventflag_resolve(const char *name) { return RUNTIME_LOOKUP(exports,name); }
void runtime_eventflag_report(void) {
    pthread_mutex_lock(&lock);
    printf("Runtime: event flags created=%zu, waits=%zu, timeouts=%zu\n",created,waits,timeouts);
    pthread_mutex_unlock(&lock);
}
