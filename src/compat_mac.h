/* macOS (Apple clang, x86_64 under Rosetta 2) counterparts of the Linux calls the runtime
 * uses. Included through runtime.h after the C library headers, like compat_win.h, so the
 * macros below only replace calls in this project's own sources. <pthread.h> and <time.h>
 * come first so later includes of them cannot see the macros. */
#ifndef ISSN_COMPAT_MAC_H
#define ISSN_COMPAT_MAC_H
#ifdef __APPLE__
#include <stdint.h>
#include <time.h>
#include <errno.h>
#include <pthread.h>
#include <sys/types.h>
#include <sys/stat.h>

/* struct stat's nanosecond times are st_*timespec on macOS (st_*tim on Linux). */
#define st_atim st_atimespec
#define st_mtim st_mtimespec
#define st_ctim st_ctimespec

#ifndef CLOCK_REALTIME_COARSE
#define CLOCK_REALTIME_COARSE CLOCK_REALTIME
#endif
#ifndef PTHREAD_RECURSIVE_MUTEX_INITIALIZER_NP
#define PTHREAD_RECURSIVE_MUTEX_INITIALIZER_NP PTHREAD_RECURSIVE_MUTEX_INITIALIZER
#endif

/* Kernel thread id (Linux gettid): the 64-bit id macOS gives each thread. */
static inline pid_t compat_gettid(void) {
    uint64_t id = 0;
    pthread_threadid_np(NULL, &id);
    return (pid_t)id;
}
#define gettid compat_gettid

/* Only the calling thread can name itself on macOS. */
#define pthread_setname_np(thread, name) ((void)(thread), pthread_setname_np(name))

/* macOS condition variables wait on CLOCK_REALTIME only (no pthread_condattr_setclock), as
 * with winpthreads: runtime_sema.c and runtime_eventflag.c pick WAIT_CLOCK = CLOCK_REALTIME. */
#define pthread_condattr_setclock(attr, clock) ((void)(attr), (clock) == CLOCK_REALTIME ? 0 : EINVAL)

/* No timed mutex/rwlock acquisition on macOS: try until the CLOCK_REALTIME deadline. */
int compat_mutex_timedlock(pthread_mutex_t *mutex, const struct timespec *deadline);
int compat_rwlock_timedrdlock(pthread_rwlock_t *lock, const struct timespec *deadline);
int compat_rwlock_timedwrlock(pthread_rwlock_t *lock, const struct timespec *deadline);
#define pthread_mutex_timedlock compat_mutex_timedlock
#define pthread_rwlock_timedrdlock compat_rwlock_timedrdlock
#define pthread_rwlock_timedwrlock compat_rwlock_timedwrlock

/* Guest memory backing (Linux memfd_create): an unlinked POSIX shared memory object. */
#define MFD_CLOEXEC 1u
int compat_memfd_create(const char *name, unsigned flags);
#define memfd_create compat_memfd_create

/* Returning pages to the pool (Linux punch hole) has no shared-memory counterpart: released
 * pages stay committed until the guest reuses them. */
#define FALLOC_FL_KEEP_SIZE 1
#define FALLOC_FL_PUNCH_HOLE 2
#define fallocate(fd, mode, offset, length) ((void)(fd), (void)(mode), (void)(offset), (void)(length), 0)

/* Linux close_range(first, ~0u, 0): closes every descriptor from first up. */
void compat_close_from(int first);

/* Rosetta 2 runs AVX/AVX2 but only reports them in CPUID to a process started with
 * ROSETTA_ADVERTISE_AVX=1: a translated process without it starts itself again with it. */
void compat_rosetta_advertise_avx(char **argv);

#endif
#endif
