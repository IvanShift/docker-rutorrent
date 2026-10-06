#define _GNU_SOURCE
#include <sys/syscall.h>
#define fstat ownership_test_fstat
#define fchownat ownership_test_fchownat
#define openat ownership_test_openat
#define readdir ownership_test_readdir
#define OWNERSHIP_TEST_HOOK
#define main ownership_program
#include "../../rootfs/usr/local/libexec/ownership-walk.c"
#undef main
#undef fstat
#undef fchownat
#undef openat
#undef readdir
int fstat(int, struct stat *);
int fchownat(int, const char *, uid_t, gid_t, int);
int openat(int, const char *, int, ...);
struct dirent *readdir(DIR *);
static bool stale_target(int fd) {
    const char *target = getenv("OWNERSHIP_TEST_STALE_INODE");
    struct stat st;
    return target && fstat(fd, &st) == 0 && st.st_ino == (ino_t)strtoumax(target, NULL, 10);
}
int ownership_test_fstat(int fd, struct stat *st) {
    const char *scenario = getenv("OWNERSHIP_TEST_SCENARIO");
    if (scenario && !strcmp(scenario, "stale-fstat") && stale_target(fd)) { errno = ESTALE; return -1; }
    return fstat(fd, st);
}
int ownership_test_fchownat(int fd, const char *name, uid_t uid, gid_t gid, int flags) {
    const char *scenario = getenv("OWNERSHIP_TEST_SCENARIO");
    if (scenario && !strncmp(scenario, "stale-", 6)) {
        if (!strcmp(scenario, "stale-chown") && stale_target(fd)) { errno = ESTALE; return -1; }
        return 0;
    }
    return fchownat(fd, name, uid, gid, flags);
}
int ownership_test_openat(int fd, const char *name, int flags, ...) {
    const char *scenario = getenv("OWNERSHIP_TEST_SCENARIO");
    if (scenario && !strcmp(scenario, "stale-readable") && !strcmp(name, ".") && stale_target(fd)) { errno = ESTALE; return -1; }
    return openat(fd, name, flags);
}
struct dirent *ownership_test_readdir(DIR *dir) {
    const char *scenario = getenv("OWNERSHIP_TEST_SCENARIO");
    if (scenario && !strcmp(scenario, "stale-readdir") && stale_target(dirfd(dir))) { errno = ESTALE; return NULL; }
    return readdir(dir);
}
static int swapped;
void ownership_checkpoint(const char *stage, int fd, int parent, const char *name) {
    const char *scenario = getenv("OWNERSHIP_TEST_SCENARIO");
    const char *outside = getenv("OWNERSHIP_TEST_OUTSIDE");
    if (!scenario) return;
    if (!strcmp(scenario, "groups") && !strcmp(stage, "service-access")) {
        gid_t group;
        if (getgroups(1, &group) != 1 || group != access_gid ||
            (uid_t)setfsuid((uid_t)-1) != access_uid || (gid_t)setfsgid((gid_t)-1) != access_gid) exit(44);
        fputs("PASS fallback uses exact service UID and one primary group\n", stdout);
    }
    if (!strcmp(scenario, "swap") && !strcmp(stage, "opened") && !strcmp(name, "victim") && !swapped++) {
        if (renameat(parent, "victim", parent, "held") || symlinkat(outside, parent, "victim")) exit(41);
    }
    if (!strcmp(scenario, "vanish") && !strcmp(stage, "entry") && !strcmp(name, "vanishing")) {
        if (unlinkat(parent, name, 0) && errno != ENOENT) exit(41);
    }
    if (!strcmp(stage, "assign")) {
        struct stat target, opened;
        char *sentinel = join(outside, "sentinel");
        if (stat(sentinel, &target) || fstat(fd, &opened)) exit(41);
        free(sentinel);
        if (target.st_dev == opened.st_dev && target.st_ino == opened.st_ino) {
            fputs("FAIL ownership walk reached the outside sentinel\n", stderr); exit(42);
        }
        if (!strcmp(scenario, "swap")) {
            const char *tree = getenv("OWNERSHIP_TEST_TREE");
            char *original = join(tree, "held/file");
            struct stat child;
            if (!stat(original, &child) && child.st_dev == opened.st_dev && child.st_ino == opened.st_ino) {
                const char *marker = getenv("OWNERSHIP_TEST_VISITED");
                FILE *record = fopen(marker, "w");
                if (!record) exit(41);
                fputs("visited\n", record); fclose(record);
            }
            free(original);
        }
        if (!strcmp(scenario, "protected") && (strstr(name, "/protected/") || (strlen(name) >= 10 && !strcmp(name + strlen(name) - 10, "/protected")))) {
            fputs("FAIL ownership walk reached a protected path\n", stderr); exit(43);
        }
    }
}
int main(int argc, char **argv) {
    // Host sandbox ancestors may have remapped owners. Exercise the production
    // visitor directly here; image cases independently test ancestor admission.
    if (argc == 5 && !strcmp(argv[1], "session") && getenv("OWNERSHIP_TEST_VISITOR_ONLY")) {
        service_uid = (uid_t)decimal(argv[2]); service_gid = (gid_t)decimal(argv[3]); session_mode = true;
        int fd = open(argv[4], O_PATH | O_DIRECTORY | O_NOFOLLOW);
        if (fd < 0) return 41;
        visit(fd, fd, "", argv[4], "", false, true);
        visit(fd, fd, "", argv[4], "", true, true);
        close(fd); return 0;
    }
    const char *scenario = getenv("OWNERSHIP_TEST_SCENARIO");
    if (scenario && !strcmp(scenario, "groups")) {
        int count = getgroups(0, NULL);
        gid_t *before = calloc((size_t)count + 1, sizeof(gid_t));
        gid_t *after = calloc((size_t)count + 1, sizeof(gid_t));
        if (count < 0 || !before || !after || getgroups(count, before) != count) return 44;
        int result = ownership_program(argc, argv);
        if (getgroups(count, after) != count || memcmp(before, after, (size_t)count * sizeof(gid_t))) return 44;
        free(before); free(after);
        fputs("PASS supervisor supplementary groups restored\n", stdout);
        return result;
    }
    return ownership_program(argc, argv);
}
