// Descriptor-bound startup ownership and native session admission. Linux only.
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <grp.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/fsuid.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

struct identity { dev_t dev; ino_t ino; };
static struct identity exclusions[64];
static size_t exclusion_count, warnings;
static const char *excluded_paths[64];
static size_t excluded_path_count;
struct retired_link { dev_t dev; ino_t parent; char *name; };
static struct retired_link *retired;
static size_t retired_count;
static uid_t service_uid;
static gid_t service_gid;
static uid_t access_uid;
static gid_t access_gid;
static bool optional, session_mode, legacy_mode, preserve_root;
#ifdef OWNERSHIP_TEST_HOOK
void ownership_checkpoint(const char *, int, int, const char *);
#define CHECKPOINT(stage, fd, parent, name) ownership_checkpoint(stage, fd, parent, name)
#else
#define CHECKPOINT(stage, fd, parent, name) ((void)0)
#endif
static void diagnostic(const char *prefix, const char *path, const char *reason) {
    fputs(prefix, stderr);
    for (const unsigned char *p = (const unsigned char *)path; *p; ++p)
        fputc(*p < 32 || *p == 127 ? '?' : *p, stderr);
    fprintf(stderr, ": %s\n", reason);
}
static void refuse(const char *path, const char *reason) {
    diagnostic("Cannot attest ownership ", path, reason);
    exit(78);
}
static void warn(const char *path, const char *reason) {
    if (++warnings <= 3) diagnostic("Warning: leaving ownership of ", path, reason);
}
// Match su-exec's single primary group; root's supplementary groups must not
// accidentally grant access that the selected service does not have.
struct credentials { uid_t uid; gid_t gid; int count; gid_t *groups; };
static struct credentials access_begin(void) {
    struct credentials old = {0};
    old.count = getgroups(0, NULL);
    if (old.count < 0) refuse("service access", "cannot inspect supplementary groups");
    old.groups = calloc((size_t)old.count + 1, sizeof(gid_t));
    if (!old.groups || getgroups(old.count, old.groups) < 0 || setgroups(1, &access_gid))
        refuse("service access", "cannot select the configured service group");
    old.gid = setfsgid(access_gid);
    old.uid = setfsuid(access_uid);
    if ((uid_t)setfsuid((uid_t)-1) != access_uid || (gid_t)setfsgid((gid_t)-1) != access_gid)
        refuse("service access", "cannot select the configured filesystem identity");
    CHECKPOINT("service-access", -1, -1, "");
    return old;
}
static void access_end(struct credentials *old) {
    setfsuid(old->uid); setfsgid(old->gid);
    if ((uid_t)setfsuid((uid_t)-1) != old->uid || (gid_t)setfsgid((gid_t)-1) != old->gid)
        refuse("service access", "cannot restore supervisor filesystem identity");
    if (setgroups((size_t)old->count, old->groups)) refuse("service access", "cannot restore supervisor groups");
    free(old->groups);
}
// NFS root_squash may deny root while permitting the configured service UID.
// Keep only the opened descriptor, restoring supervisor credentials immediately.
static int service_openat(int parent, const char *name, int flags) {
    int fd = openat(parent, name, flags | O_CLOEXEC);
    if (fd >= 0 || geteuid() != 0 || (errno != EACCES && errno != EPERM)) return fd;
    struct credentials old = access_begin();
    fd = openat(parent, name, flags | O_CLOEXEC);
    int error = errno;
    access_end(&old);
    errno = error;
    return fd;
}
static int service_unlinkat(int parent, const char *name) {
    int result = unlinkat(parent, name, 0);
    if (result == 0 || geteuid() != 0 || (errno != EACCES && errno != EPERM)) return result;
    struct credentials old = access_begin();
    result = unlinkat(parent, name, 0);
    int error = errno; access_end(&old); errno = error;
    return result;
}
static bool missing(int error) { return error == ENOENT || error == ESTALE; }
static bool restricted(int error) { return error == EROFS || error == EACCES || error == EPERM; }
static bool excluded(const struct stat *st, const char *path) {
    for (size_t i = 0; i < excluded_path_count; ++i) {
        size_t n = strlen(excluded_paths[i]);
        if (!strncmp(path, excluded_paths[i], n) && (path[n] == 0 || path[n] == '/')) return true;
    }
    for (size_t i = 0; i < exclusion_count; ++i)
        if (st->st_dev == exclusions[i].dev && st->st_ino == exclusions[i].ino) return true;
    return false;
}
static void assign(int fd, const struct stat *st, const char *path) {
    CHECKPOINT("assign", fd, fd, path);
    if (st->st_uid == service_uid && st->st_gid == service_gid) return;
    // AT_EMPTY_PATH binds both ownership fields to the same opened inode.
    if (fchownat(fd, "", service_uid, service_gid, AT_EMPTY_PATH | AT_SYMLINK_NOFOLLOW) == 0) return;
    if (optional && missing(errno)) return;
    if (optional && restricted(errno)) { warn(path, "read-only or restricted mount; the service must already have access"); return; }
    refuse(path, "cannot assign service UID:GID; pre-provision ownership on restricted mounts while stopped");
}
static int readable(int fd, const struct stat *st) {
    if (S_ISDIR(st->st_mode)) return service_openat(fd, ".", O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
    char name[64];
    snprintf(name, sizeof(name), "/proc/self/fd/%d", fd);
    // This link belongs to the kernel and references our pinned descriptor.
    return service_openat(AT_FDCWD, name, O_RDONLY | O_NONBLOCK);
}
static void normalize(int fd, const struct stat *st, const char *path, mode_t mode) {
    if ((st->st_mode & 07777) == mode) return;
    int rw = readable(fd, st);
    if (rw < 0) refuse(path, "cannot open the admitted inode for permission repair");
    struct stat current;
    if (fstat(rw, &current) || current.st_dev != st->st_dev || current.st_ino != st->st_ino)
        refuse(path, "descriptor identity changed during permission repair");
    int changed = fchmod(rw, mode);
    if (changed && geteuid() == 0 && (errno == EACCES || errno == EPERM)) {
        struct credentials old = access_begin();
        changed = fchmod(rw, mode);
        int error = errno; access_end(&old); errno = error;
    }
    if (changed) { close(rw); refuse(path, "cannot normalize trusted session permissions; repair while stopped"); }
    close(rw);
}
static bool hex_lower(const char *p, size_t n) {
    for (size_t i = 0; i < n; ++i) if (!((p[i] >= '0' && p[i] <= '9') || (p[i] >= 'a' && p[i] <= 'f'))) return false;
    return true;
}
static bool hex_any(const char *p, size_t n) {
    for (size_t i = 0; i < n; ++i) if (!((p[i] >= '0' && p[i] <= '9') || (p[i] >= 'a' && p[i] <= 'f') || (p[i] >= 'A' && p[i] <= 'F'))) return false;
    return true;
}
static bool hash_name(const char *name) {
    return strlen(name) == 46 && hex_any(name, 40) && strcmp(name + 40, ".claim") == 0;
}
// Only the attested historical publication pair is repairable. Unknown aliases
// and a third link remain an explicit refusal rather than an ownership sweep.
static void recover_claim(int parent, int fd, const char *name, const char *path, struct stat *st) {
    if (!hash_name(name) || !S_ISREG(st->st_mode) || (st->st_mode & 07777) != 0600 ||
        (st->st_uid != 0 && st->st_uid != service_uid) || st->st_nlink != 2 || st->st_size != 128)
        refuse(path, "private record is not single-linked or an exact historical claim pair");
    struct stat dir;
    if (fstat(parent, &dir) || !S_ISDIR(dir.st_mode) || (dir.st_uid != 0 && dir.st_uid != service_uid) ||
        (dir.st_mode & 07777) != 0700) refuse(path, "claim parent is not a private owned directory");
    int read_fd = readable(fd, st);
    char payload[129];
    ssize_t got = read_fd < 0 ? -1 : pread(read_fd, payload, sizeof(payload), 0);
    if (read_fd >= 0) close(read_fd);
    if (got != 128 || !hex_lower(payload, 32) || payload[32] != ':' || !hex_lower(payload + 33, 16) ||
        payload[49] != '\n' || !hex_lower(payload + 50, 32) || payload[82] != '\n' ||
        (payload[83] != '0' && payload[83] != '1') || (payload[84] != '0' && payload[84] != '1') ||
        (payload[85] != '0' && payload[85] != '1') || payload[86] != '\n' ||
        !hex_any(payload + 87, 40) || payload[127] != '\n') refuse(path, "historical claim payload is malformed");
    char twin[84];
    snprintf(twin, sizeof(twin), "%s.%.32s.tmp", name, payload);
    int alias = service_openat(parent, twin, O_PATH | O_NOFOLLOW);
    struct stat other;
    if (alias < 0 || fstat(alias, &other) || !S_ISREG(other.st_mode) ||
        other.st_dev != st->st_dev || other.st_ino != st->st_ino || other.st_uid != st->st_uid ||
        (other.st_mode & 07777) != 0600 || other.st_nlink != 2 || other.st_size != 128)
        refuse(path, "historical claim counterpart does not match the pinned inode");
    close(alias);
    if (service_unlinkat(parent, twin)) refuse(path, "cannot retire the attested claim counterpart");
    int directory = readable(parent, &dir);
    if (directory < 0 || fsync(directory)) refuse(path, "cannot durably retire the attested claim counterpart");
    close(directory);
    struct retired_link *next = realloc(retired, (retired_count + 1) * sizeof(*retired));
    if (!next) refuse(path, "cannot record the retired claim alias");
    retired = next;
    retired[retired_count++] = (struct retired_link){dir.st_dev, dir.st_ino, strdup(twin)};
    if (!retired[retired_count - 1].name) refuse(path, "cannot record the retired claim alias");
    if (fstat(fd, st) || st->st_nlink != 1) refuse(path, "claim link count changed during recovery");
    diagnostic("Recovered historical claim ", path, "retired matching temporary link");
}
static void validate_session(int fd, int parent, const char *name, const char *path, const char *relative, struct stat *st) {
    mode_t mode = st->st_mode & 07777;
    if (st->st_uid != 0 && st->st_uid != service_uid) refuse(path, "foreign session owner; repair ownership while stopped");
    if (!*relative) {
        if (!S_ISDIR(st->st_mode) || (mode & 0022) || (mode & 04000)) refuse(path, "unsafe session directory");
        normalize(fd, st, path, mode & 01777);
        return;
    }
    size_t head_len = strcspn(relative, "/");
    bool stop = head_len == strlen(".rutorrent-stop-intents") && !strncmp(relative, ".rutorrent-stop-intents", head_len);
    bool retrackers = head_len == strlen(".retrackers-migration") && !strncmp(relative, ".retrackers-migration", head_len);
    bool autotools = head_len == strlen(".autotools-file-jobs") && !strncmp(relative, ".autotools-file-jobs", head_len);
    bool journal = stop || retrackers || autotools;
    if (journal && relative[head_len] == '\0') {
        if (!S_ISDIR(st->st_mode) || (mode & 04777) != 0700) refuse(path, "unsafe private journal directory");
        normalize(fd, st, path, 0700);
        return;
    }
    if (!S_ISREG(st->st_mode) && !S_ISDIR(st->st_mode)) refuse(path, "session entries must be regular data, without symlinks");
    if (journal && S_ISDIR(st->st_mode)) refuse(path, "private journal contains a nested directory");
    if (stop && st->st_nlink == 2) recover_claim(parent, fd, name, path, st);
    if (!journal && st->st_uid != service_uid) refuse(path, "root or foreign-owned ordinary session data; use offline UID repair");
    if (S_ISDIR(st->st_mode)) {
        if ((mode & 0022) || (mode & 04000)) refuse(path, "unsafe ordinary session directory");
    } else {
        if (st->st_nlink != 1) refuse(path, "session file is not single-linked");
        if (journal && !autotools) {
            if (mode != 0600) refuse(path, "private journal record requires mode0600");
        } else if (mode & 0022) normalize(fd, st, path, mode & 07755);
    }
}
static char *join(const char *parent, const char *name) {
    char *result;
    if (asprintf(&result, "%s%s%s", parent, *parent ? "/" : "", name) < 0) refuse(parent, "allocation failed");
    return result;
}
static void visit(int fd, int parent, const char *name, const char *path, const char *relative, bool handoff, bool root) {
    struct stat st;
    if (fstat(fd, &st)) {
        if (optional && !root && missing(errno)) return;
        refuse(path, "opened inode cannot be inspected");
    }
    if (excluded(&st, path)) return;
    if (session_mode) {
        // Enumeration can encounter the old temporary link before its canonical
        // claim. Recover through the canonical name; never skip an unknown pair.
        const char *slash = strchr(relative, '/');
        if (slash && (size_t)(slash - relative) == strlen(".rutorrent-stop-intents") &&
            !strncmp(relative, ".rutorrent-stop-intents", strlen(".rutorrent-stop-intents")) &&
            strlen(name) == 83 && hex_any(name, 40) && !strncmp(name + 40, ".claim.", 7) &&
            hex_lower(name + 47, 32) && !strcmp(name + 79, ".tmp") && st.st_nlink == 2) {
            char canonical[47]; memcpy(canonical, name, 46); canonical[46] = 0;
            int original = service_openat(parent, canonical, O_PATH | O_NOFOLLOW);
            struct stat current;
            if (original < 0 || fstat(original, &current) || current.st_dev != st.st_dev || current.st_ino != st.st_ino)
                refuse(path, "temporary claim has no matching canonical inode");
            recover_claim(parent, original, canonical, path, &current);
            close(original);
            return;
        }
        validate_session(fd, parent, name, path, relative, &st);
    }
    CHECKPOINT("opened", fd, parent, name);
    if (handoff && !(root && preserve_root)) assign(fd, &st, path);
    if (!S_ISDIR(st.st_mode)) return;
    int reader = readable(fd, &st);
    if (reader < 0) {
        if (optional && !root && missing(errno)) return;
        if (optional && restricted(errno)) { warn(path, "directory is restricted; pre-provision service access"); return; }
        refuse(path, "cannot enumerate directory with root or the selected service identity");
    }
    DIR *dir = fdopendir(reader);
    if (!dir) { close(reader); refuse(path, "cannot enumerate directory"); }
    struct dirent *entry;
    errno = 0;
    while ((entry = readdir(dir))) {
        if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..")) continue;
        char *child = join(path, entry->d_name);
        char *rel = join(relative, entry->d_name);
        CHECKPOINT("entry", fd, fd, entry->d_name);
        int child_fd = -1;
        for (int attempt = 0; attempt < 3; ++attempt) {
            child_fd = service_openat(fd, entry->d_name, O_PATH | O_NOFOLLOW);
            if (child_fd >= 0 || errno != ESTALE) break;
        }
        if (child_fd < 0) {
            bool retired_here = false;
            struct stat parent_stat;
            if (session_mode && missing(errno) && !fstat(fd, &parent_stat)) {
                for (size_t i = 0; i < retired_count; ++i)
                    if (retired[i].dev == parent_stat.st_dev && retired[i].parent == parent_stat.st_ino &&
                        !strcmp(retired[i].name, entry->d_name)) retired_here = true;
            }
            if ((optional && missing(errno)) || retired_here) { free(child); free(rel); errno = 0; continue; }
            if (optional && restricted(errno)) { warn(child, "entry is restricted; pre-provision service access"); free(child); free(rel); errno = 0; continue; }
            refuse(child, "directory entry cannot be opened without following links");
        }
        visit(child_fd, fd, entry->d_name, child, rel, handoff, false);
        close(child_fd);
        free(child); free(rel);
        errno = 0;
    }
    if (errno && !(optional && !root && missing(errno))) refuse(path, "directory enumeration failed");
    closedir(dir);
}
static int root_fd(const char *path) {
    if (path[0] != '/') { fputs("absolute ownership root required\n", stderr); exit(64); }
    int fd = open("/", O_PATH | O_DIRECTORY | O_CLOEXEC);
    char *copy = strdup(path), *state = NULL;
    if (fd < 0 || !copy) refuse(path, "cannot open traversal root");
    for (char *part = strtok_r(copy, "/", &state); part; part = strtok_r(NULL, "/", &state)) {
        if (!strcmp(part, ".") || !strcmp(part, "..")) { fputs("ownership traversal components are invalid\n", stderr); exit(64); }
        struct stat st;
        // Session ancestors: root or the service UID, group/other write only
        // with sticky. service-runtime.c trusted_directory (root-only, never
        // sticky) and logrotate's /tmp rule (root, sticky allowed) are narrower.
        if (session_mode && (fstat(fd, &st) || (st.st_uid != 0 && st.st_uid != service_uid) ||
            ((st.st_mode & 0022) && !(st.st_mode & S_ISVTX)))) refuse(path, "unsafe session ancestor");
        int next = service_openat(fd, part, O_PATH | O_NOFOLLOW | O_DIRECTORY);
        if (next < 0) refuse(path, "ownership root or ancestor is not a real accessible directory");
        close(fd); fd = next;
    }
    free(copy);
    return fd;
}
static uintmax_t decimal(const char *value) {
    if (!*value) exit(64);
    for (const char *p = value; *p; ++p) if (*p < '0' || *p > '9') exit(64);
    char *end;
    errno = 0;
    uintmax_t result = strtoumax(value, &end, 10);
    if (errno || *end || result >= UINT32_MAX) exit(64);
    return result;
}
int main(int argc, char **argv) {
    if (argc < 5) return 64;
    optional = !strcmp(argv[1], "shared") || !strcmp(argv[1], "single-optional");
    session_mode = !strcmp(argv[1], "session");
    legacy_mode = !strcmp(argv[1], "legacy");
    bool single = !strcmp(argv[1], "single") || !strcmp(argv[1], "single-optional");
    if (!optional && !session_mode && !legacy_mode && !single) return 64;
    service_uid = (uid_t)decimal(argv[2]);
    service_gid = (gid_t)decimal(argv[3]);
    access_uid = service_uid; access_gid = service_gid;
    for (int i = 5; i < argc; ++i) {
        if (!strcmp(argv[i], "--access-uid") && ++i < argc) access_uid = (uid_t)decimal(argv[i]);
        else if (!strcmp(argv[i], "--access-gid") && ++i < argc) access_gid = (gid_t)decimal(argv[i]);
        else if (!strcmp(argv[i], "--preserve-root")) preserve_root = true;
        else if (!strcmp(argv[i], "--exclude-path") && ++i < argc && excluded_path_count < 64) {
            if (argv[i][0] != '/') return 64;
            excluded_paths[excluded_path_count++] = argv[i];
        }
        else if (!strcmp(argv[i], "--exclude") && ++i < argc && exclusion_count < 64) {
            uintmax_t dev, ino; char tail;
            if (sscanf(argv[i], "%ju:%ju%c", &dev, &ino, &tail) != 2) return 64;
            exclusions[exclusion_count++] = (struct identity){ (dev_t)dev, (ino_t)ino };
        } else return 64;
    }
    int fd;
    if (single) {
        char *copy = strdup(argv[4]);
        if (!copy) return 78;
        char *name = strrchr(copy, '/');
        if (!name || !name[1]) return 64;
        *name++ = '\0';
        if (!strcmp(name, ".") || !strcmp(name, "..")) return 64;
        int parent = root_fd(*copy ? copy : "/");
        fd = service_openat(parent, name, O_PATH | O_NOFOLLOW);
        if (fd < 0) refuse(argv[4], "ownership leaf cannot be opened");
        struct stat st;
        if (fstat(fd, &st)) refuse(argv[4], "ownership leaf cannot be inspected");
        if (!excluded(&st, argv[4])) assign(fd, &st, argv[4]);
        close(parent); free(copy);
    } else {
        fd = root_fd(argv[4]);
        if (session_mode) visit(fd, fd, "", argv[4], "", false, true);
        visit(fd, fd, "", argv[4], "", true, true);
        if (session_mode || legacy_mode) {
            struct stat lock;
            if (fstatat(fd, "rtorrent.lock", &lock, AT_SYMLINK_NOFOLLOW) == 0) {
                if (!S_ISREG(lock.st_mode) || lock.st_uid != service_uid || lock.st_nlink != 1)
                    refuse(argv[4], "stale session lock is not an owned single-link file");
                if (service_unlinkat(fd, "rtorrent.lock")) refuse(argv[4], "cannot remove the stale session lock");
            } else if (errno != ENOENT) refuse(argv[4], "cannot inspect the stale session lock");
        }
    }
    close(fd);
    for (size_t i = 0; i < retired_count; ++i) free(retired[i].name);
    free(retired);
    if (warnings > 3) fprintf(stderr, "Warning: %zu additional ownership assignments refused on read-only or restricted mounts; the service must already have access\n", warnings - 3);
    return 0;
}
