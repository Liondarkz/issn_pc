/* macOS implementations of compat_mac.h. */
#ifdef __APPLE__
#include "compat_mac.h"
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/sysctl.h>
#include <unistd.h>

static int expired(const struct timespec *deadline) {
    struct timespec now;
    clock_gettime(CLOCK_REALTIME, &now);
    return now.tv_sec > deadline->tv_sec || (now.tv_sec == deadline->tv_sec && now.tv_nsec >= deadline->tv_nsec);
}
/* Polls the try variant with a short sleep: the guest's timed waits are rare and short, and
 * a deadline in the past still gets one attempt (POSIX). */
static int timed_try(int (*attempt)(void *), void *object, const struct timespec *deadline) {
    if (deadline->tv_nsec < 0 || deadline->tv_nsec >= 1000000000) return EINVAL;
    for (;;) {
        int e = attempt(object);
        if (e != EBUSY) return e;
        if (expired(deadline)) return ETIMEDOUT;
        struct timespec nap = {0, 200000};
        nanosleep(&nap, NULL);
    }
}
static int try_mutex(void *m) { return pthread_mutex_trylock(m); }
static int try_read(void *l) { return pthread_rwlock_tryrdlock(l); }
static int try_write(void *l) { return pthread_rwlock_trywrlock(l); }
int compat_mutex_timedlock(pthread_mutex_t *mutex, const struct timespec *deadline) {
    return timed_try(try_mutex, mutex, deadline);
}
int compat_rwlock_timedrdlock(pthread_rwlock_t *lock, const struct timespec *deadline) {
    return timed_try(try_read, lock, deadline);
}
int compat_rwlock_timedwrlock(pthread_rwlock_t *lock, const struct timespec *deadline) {
    return timed_try(try_write, lock, deadline);
}

int compat_memfd_create(const char *name, unsigned flags) {
    (void)name;
    static unsigned counter;
    char path[32]; /* PSHMNAMLEN is 31 */
    for (int attempt = 0; attempt < 100; ++attempt) {
        snprintf(path, sizeof(path), "/issn.%d.%u", (int)getpid(),
                 __atomic_fetch_add(&counter, 1, __ATOMIC_RELAXED));
        int fd = shm_open(path, O_RDWR | O_CREAT | O_EXCL, 0600);
        if (fd >= 0) {
            shm_unlink(path);
            if (flags & MFD_CLOEXEC) fcntl(fd, F_SETFD, FD_CLOEXEC);
            return fd;
        }
        if (errno != EEXIST) return -1;
    }
    return -1;
}

void compat_close_from(int first) {
    struct rlimit limit;
    int last = 65536;
    if (!getrlimit(RLIMIT_NOFILE, &limit) && limit.rlim_cur != RLIM_INFINITY && limit.rlim_cur < (rlim_t)last)
        last = (int)limit.rlim_cur;
    for (int fd = first; fd < last; ++fd) close(fd);
}

void compat_rosetta_advertise_avx(char **argv) {
    int translated = 0;
    size_t size = sizeof translated;
    if (sysctlbyname("sysctl.proc_translated", &translated, &size, NULL, 0) || !translated) return;
    if (getenv("ROSETTA_ADVERTISE_AVX")) return; /* already set, or chosen by the user */
    char path[PATH_MAX];
    uint32_t length = sizeof path;
    if (_NSGetExecutablePath(path, &length)) return;
    setenv("ROSETTA_ADVERTISE_AVX", "1", 1);
    execv(path, argv);
    unsetenv("ROSETTA_ADVERTISE_AVX"); /* execv failed: carry on, cpu_check reports AVX */
}
#endif
