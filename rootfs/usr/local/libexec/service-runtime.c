#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

static int refuse(const char *reason)
{
    fprintf(stderr, "service-runtime: %s\n", reason);
    return 1;
}

static int pidfd_open_process(pid_t pid)
{
    return (int)syscall(SYS_pidfd_open, pid, 0);
}

static bool alive(int fd)
{
    struct pollfd item = {.fd = fd, .events = POLLIN};
    return poll(&item, 1, 0) == 0;
}

static int pidfd_signal(int fd, int signal_number)
{
    return (int)syscall(SYS_pidfd_send_signal, fd, signal_number, NULL, 0);
}

/* Only the container entry process shares the terminal's process group.
   s6-supervise's intentional terminal SIGINT behavior cannot race shutdown. */
static int supervise(const char *directory)
{
    sigset_t signals, original;
    sigemptyset(&signals);
    sigaddset(&signals, SIGCHLD);
    sigaddset(&signals, SIGINT);
    sigaddset(&signals, SIGTERM);
    sigaddset(&signals, SIGHUP);
    if (sigprocmask(SIG_BLOCK, &signals, &original) < 0
        || prctl(PR_SET_CHILD_SUBREAPER, 1) < 0)
        return refuse("unable-to-isolate-supervision");
    pid_t child = fork();
    if (child < 0) return refuse("unable-to-start-supervision");
    if (child == 0) {
        if (setsid() < 0 || sigprocmask(SIG_SETMASK, &original, NULL) < 0)
            _exit(111);
        execl("/usr/bin/s6-svscan", "s6-svscan", directory, (char *)NULL);
        _exit(111);
    }
    /* An unreaped direct child cannot have its PID reused. */
    int fd = pidfd_open_process(child);
    bool stopping = false;
    for (;;) {
        int status;
        pid_t reaped;
        while ((reaped = waitpid(-1, &status, WNOHANG)) > 0) {
            if (reaped == child) {
                if (fd >= 0) close(fd);
                return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
            }
        }
        int received = sigwaitinfo(&signals, NULL);
        if (received < 0) {
            if (errno == EINTR) continue;
            return refuse("unable-to-wait-for-supervision");
        }
        if (received == SIGCHLD) continue;
        if (received == SIGHUP) {
            if (fd >= 0) (void)pidfd_signal(fd, SIGHUP);
            else (void)kill(child, SIGHUP);
        } else if (!stopping) {
            stopping = true;
            if (fd >= 0) (void)pidfd_signal(fd, SIGTERM);
            else (void)kill(child, SIGTERM);
        }
    }
}

#ifdef SERVICE_RUNTIME_TEST_HOOK
static void pause_after_identity(void)
{
    const char *ready = getenv("SERVICE_RUNTIME_TEST_READY");
    const char *resume = getenv("SERVICE_RUNTIME_TEST_RESUME");
    if (!ready || !resume) return;
    int fd = open(ready, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (fd < 0) _exit(2);
    close(fd);
    for (int i = 0; i < 5000 && access(resume, F_OK) != 0; ++i) usleep(1000);
    if (access(resume, F_OK) != 0) _exit(2);
}
#endif

static int reopen(const char *service)
{
    const char *pidfile, *executable, *prefix;
    if (strcmp(service, "nginx") == 0) {
        pidfile = "/run/nginx/nginx.pid";
        executable = "/usr/sbin/nginx";
        prefix = "nginx: master process ";
    } else if (strcmp(service, "php85") == 0) {
        pidfile = "/run/php/php85-fpm.pid";
        executable = "/usr/sbin/php-fpm85";
        prefix = "php-fpm: master process ";
    } else return refuse("invalid-reopen-service");
#ifdef SERVICE_RUNTIME_TEST_HOOK
    const char *fixture = getenv("SERVICE_RUNTIME_TEST_PIDFILE");
    if (fixture) {
        pidfile = fixture;
        executable = getenv("SERVICE_RUNTIME_TEST_EXECUTABLE");
        prefix = getenv("SERVICE_RUNTIME_TEST_PREFIX");
        if (!executable || !prefix) return 1;
    }
#endif
    char text[64], path[96], command[4096];
    int input = open(pidfile, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (input < 0) return refuse("reopen-pidfile-unavailable");
    struct stat metadata;
    ssize_t count = read(input, text, sizeof(text) - 1);
    bool regular = fstat(input, &metadata) == 0 && S_ISREG(metadata.st_mode)
        && metadata.st_nlink == 1;
    close(input);
    if (!regular || count <= 0 || count >= (ssize_t)sizeof(text) - 1)
        return refuse("reopen-invalid-pidfile");
    text[count] = '\0';
    for (ssize_t i = 0; i < count; ++i) {
        if (text[i] >= '0' && text[i] <= '9') continue;
        if (text[i] == '\n' && i == count - 1) continue;
        return refuse("reopen-invalid-pid");
    }
    char *end;
    errno = 0;
    long number = strtol(text, &end, 10);
    if (end == text || errno || number <= 1 || number > INT_MAX
        || (*end != '\0' && !(end[0] == '\n' && end[1] == '\0')))
        return refuse("reopen-invalid-pid");
    int fd = pidfd_open_process((pid_t)number);
    if (fd < 0) return refuse("reopen-pidfd-unavailable");
    struct stat actual, expected;
    snprintf(path, sizeof(path), "/proc/%ld/exe", number);
    bool match = stat(path, &actual) == 0 && stat(executable, &expected) == 0
        && actual.st_dev == expected.st_dev && actual.st_ino == expected.st_ino;
    snprintf(path, sizeof(path), "/proc/%ld/cmdline", number);
    input = open(path, O_RDONLY | O_CLOEXEC);
    count = input < 0 ? -1 : read(input, command, sizeof(command) - 1);
    if (input >= 0) close(input);
    if (count > 0) command[count] = '\0';
    match = match && count > 0 && (size_t)count >= strlen(prefix)
        && strncmp(command, prefix, strlen(prefix)) == 0 && alive(fd);
    if (!match) {
        close(fd);
        return refuse("refusing-reopen identity-mismatch");
    }
#ifdef SERVICE_RUNTIME_TEST_HOOK
    pause_after_identity();
    /* A fixture can model numeric PID reuse without exhausting the host PID
       namespace. Production builds contain neither this hook nor its env API. */
    const char *replacement = getenv("SERVICE_RUNTIME_TEST_REPLACEMENT_FILE");
    if (replacement) {
        FILE *fixture = fopen(replacement, "r");
        if (!fixture || fscanf(fixture, "%ld", &number) != 1) _exit(2);
        fclose(fixture);
    }
#endif
    /* /proc can change after validation; the signal still targets this pidfd. */
    int status = pidfd_signal(fd, SIGUSR1);
    close(fd);
    return status == 0 ? 0 : refuse("reopen-target-gone-or-signal-refused");
}

static int trusted_directory(int parent, const char *name)
{
    int fd = openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    struct stat metadata;
    if (fd < 0) return -1;
    if (fstat(fd, &metadata) < 0 || metadata.st_uid != 0 || (metadata.st_mode & 0022)) {
        close(fd);
        return -1;
    }
    return fd;
}

/* Monitor state is boot-local and independent of optional authentication data.
   Only the root supervisor may create/use this private directory. */
static int publication_directory(bool health, bool create)
{
    int directory = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    const char *auth[] = {"config", "nginx", "passwd"};
    const char *monitor[] = {"run", "rutorrent-watchdog"};
    const char **components = health ? monitor : auth;
    size_t count = health ? 2 : 3;
    if (directory < 0) return -1;
    for (size_t i = 0; i < count; ++i) {
        if (health && create && i == count - 1
            && mkdirat(directory, components[i], 0700) < 0 && errno != EEXIST) {
            close(directory);
            return -1;
        }
        int next = trusted_directory(directory, components[i]);
        close(directory);
        if (next < 0) return -1;
        directory = next;
    }
    struct stat metadata;
    if (health && (fstat(directory, &metadata) < 0 || (metadata.st_mode & 07777) != 0700)) {
        close(directory);
        return -1;
    }
    return directory;
}

static int prepare_health(void)
{
    if (geteuid() != 0) return refuse("health-monitor-requires-root");
    int directory = publication_directory(true, true);
    if (directory < 0) return refuse("unsafe-health-directory");
    close(directory);
    return 0;
}

static int publish_file(const char *temporary, bool health)
{
    const char *target = health ? ".service-health.json" : "rutorrent_passwd";
    const char *prefix = health ? ".service-health.json.tmp." : "rutorrent_passwd.tmp.";
    size_t prefix_size = strlen(prefix);
    if (geteuid() != 0 || strncmp(temporary, prefix, prefix_size) != 0
        || strchr(temporary, '/') || !temporary[prefix_size])
        return refuse(health ? "invalid-health-publication" : "invalid-auth-publication");
    int directory = publication_directory(health, false);
    if (directory < 0) return refuse(health ? "unsafe-health-directory" : "unsafe-auth-directory");
    int fd = openat(directory, temporary, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    struct stat metadata, previous;
    if (fd < 0 || fstat(fd, &metadata) < 0 || !S_ISREG(metadata.st_mode)
        || metadata.st_nlink != 1 || metadata.st_uid != 0
        || (metadata.st_mode & 07777) != (health ? 0600 : 0640) || metadata.st_size > 1048576) {
        if (fd >= 0) close(fd);
        close(directory);
        return refuse("unsafe-auth-candidate");
    }
    if (fstatat(directory, target, &previous, AT_SYMLINK_NOFOLLOW) == 0) {
        if (!S_ISREG(previous.st_mode) || previous.st_nlink != 1 || previous.st_uid != 0
            || (previous.st_mode & 07777) != (health ? 0600 : 0640)) {
            close(fd);
            close(directory);
            return refuse("unsafe-auth-target");
        }
    } else if (errno != ENOENT) {
        close(fd);
        close(directory);
        return refuse("auth-target-unavailable");
    }
    sigset_t signals, original;
    sigemptyset(&signals);
    sigaddset(&signals, SIGHUP);
    sigaddset(&signals, SIGINT);
    sigaddset(&signals, SIGTERM);
    int status = sigprocmask(SIG_BLOCK, &signals, &original);
    if (status == 0) {
        status = fsync(fd);
        if (status == 0) status = renameat(directory, temporary, directory, target);
        if (status == 0) status = fsync(directory);
        (void)sigprocmask(SIG_SETMASK, &original, NULL);
    }
    close(fd);
    close(directory);
    return status == 0 ? 0 : refuse("auth-publication-durability-uncertain");
}

/* Positive stopped-state evidence permits SIGCONT, never a timeout-based kill. */
static int resume_rtorrent(const char *text)
{
    char *end, path[80], line[4096];
    errno = 0;
    long pid = strtol(text, &end, 10);
    if (!*text || *end || errno || pid <= 1 || pid > INT_MAX)
        return refuse("invalid-daemon-pid");
    int fd = pidfd_open_process((pid_t)pid);
    if (fd < 0) return refuse("daemon-pidfd-unavailable");
    struct stat actual, expected;
    snprintf(path, sizeof(path), "/proc/%ld/exe", pid);
    bool match = stat(path, &actual) == 0 && stat("/usr/local/bin/rtorrent", &expected) == 0
        && actual.st_dev == expected.st_dev && actual.st_ino == expected.st_ino;
    snprintf(path, sizeof(path), "/proc/%ld/stat", pid);
    FILE *input = fopen(path, "r");
    bool read_ok = input && fgets(line, sizeof(line), input);
    if (input) fclose(input);
    char *closing = read_ok ? strrchr(line, ')') : NULL;
    bool stopped = closing && closing[1] == ' ' && (closing[2] == 'T' || closing[2] == 't');
    int status = match && stopped && alive(fd) ? pidfd_signal(fd, SIGCONT) : -1;
    close(fd);
    return status == 0 ? 0 : refuse("daemon-resume-refused");
}

int main(int argc, char **argv)
{
    if (argc == 3 && strcmp(argv[1], "supervise") == 0) return supervise(argv[2]);
    if (argc == 3 && strcmp(argv[1], "reopen") == 0) return reopen(argv[2]);
    if (argc == 3 && strcmp(argv[1], "publish-auth") == 0) return publish_file(argv[2], false);
    if (argc == 2 && strcmp(argv[1], "prepare-health") == 0) return prepare_health();
    if (argc == 3 && strcmp(argv[1], "publish-health") == 0) return publish_file(argv[2], true);
    if (argc == 3 && strcmp(argv[1], "resume-rtorrent") == 0) return resume_rtorrent(argv[2]);
    return refuse("invalid-arguments");
}
