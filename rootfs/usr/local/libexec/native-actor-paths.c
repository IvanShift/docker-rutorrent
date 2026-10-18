// Fixed Docker actor paths: build-only normalization and read-only admission.
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/xattr.h>
#include <unistd.h>

struct directory { const char *name; int parent; int fd; };
static struct directory directories[] = {
    {"/", -1, -1}, {"usr", 0, -1}, {"bin", 1, -1},
    {"rutorrent", 0, -1}, {"app", 3, -1}, {"plugins", 4, -1},
    {"retrackers", 5, -1}, {"autotools", 5, -1}, {"erasedata", 5, -1}
};
static const char *directory_paths[] = {
    "/", "/usr", "/usr/bin", "/rutorrent", "/rutorrent/app",
    "/rutorrent/app/plugins", "/rutorrent/app/plugins/retrackers",
    "/rutorrent/app/plugins/autotools", "/rutorrent/app/plugins/erasedata"
};
// Old published apps ship only update.php. Any other actor means the native
// ABI, which startup admits only as the whole set.
static const struct { int parent; const char *name, *path; } actors[] = {
    {6, "update.php", "/rutorrent/app/plugins/retrackers/update.php"},
    {6, "dispatch.php", "/rutorrent/app/plugins/retrackers/dispatch.php"},
    {7, "move_worker.php", "/rutorrent/app/plugins/autotools/move_worker.php"},
    {8, "collect_worker.php", "/rutorrent/app/plugins/erasedata/collect_worker.php"}
};
static uid_t root_owner;
static gid_t root_group;
static bool normalize, bootstrap_root;

static void refuse(const char *path, const char *reason)
{
    fprintf(stderr, "native-actor-paths: refused %s: %s\n", path, reason);
    exit(78);
}

static bool trusted_owner(uid_t owner, bool root_only)
{
    return owner == root_owner || (!root_only && owner == geteuid());
}

static void inspect_directory(int fd, const char *path, bool root_only, bool repairable)
{
    struct stat metadata;
    if (fstat(fd, &metadata) || !S_ISDIR(metadata.st_mode)
        || !trusted_owner(metadata.st_uid, root_only))
        refuse(path, "directory type or owner is unsafe");
    // Build repairs only the listed non-root directories. Runtime admission
    // follows native's sticky-ancestor rule and never changes their modes.
    if (!repairable && (metadata.st_mode & 0022)
        && !(metadata.st_mode & S_ISVTX))
        refuse(path, "directory grants group or other write access");
}

static int open_leaf(int parent, const char *name, const char *path,
                     bool executable, bool optional)
{
    int fd = openat(parent, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) {
        if (optional && errno == ENOENT) return -1;
        refuse(path, "leaf is missing or cannot be opened without following links");
    }
    struct stat metadata;
    if (fstat(fd, &metadata) || !S_ISREG(metadata.st_mode) || metadata.st_nlink != 1
        || !trusted_owner(metadata.st_uid, normalize || (executable && bootstrap_root)))
        refuse(path, "leaf type, owner or link count is unsafe");
    if (!normalize && (metadata.st_mode & 0022))
        refuse(path, "leaf grants group or other write access");
    if (executable) {
        if ((!normalize && !(metadata.st_mode & 0111))
            || (metadata.st_mode & (S_ISUID | S_ISGID)))
            refuse(path, "executable mode is unsafe");
        if (!normalize) {
            // Admit the pinned inode for the actual identity, including ACLs.
            // Unsupported kernels refuse visibly rather than weaken admission.
            if (syscall(SYS_faccessat2, fd, "", X_OK, AT_EMPTY_PATH | AT_EACCESS))
                refuse(path, "executable is unavailable to the checking identity or access inspection is unsupported");
        }
        errno = 0;
        if (fgetxattr(fd, "security.capability", NULL, 0) >= 0
            || (errno != ENODATA && errno != ENOTSUP))
            refuse(path, "executable capabilities exist or cannot be inspected");
    }
    return fd;
}

static void set_mode(int fd, const char *path, mode_t mode)
{
    // Write only differing metadata: any chown or chmod copies an overlay
    // lower inode, all of php85 included, into the build layer.
    struct stat metadata;
    if (fstat(fd, &metadata)
        || ((metadata.st_uid != root_owner || metadata.st_gid != root_group)
            && fchown(fd, root_owner, root_group))
        || ((metadata.st_mode & 07777) != mode && fchmod(fd, mode))
        || fstat(fd, &metadata) || metadata.st_uid != root_owner
        || metadata.st_gid != root_group || (metadata.st_mode & 07777) != mode)
        refuse(path, "unable to normalize the admitted inode");
}

static const char *marker_name = "rutorrent-native-actor-paths-required";
static int marker_local = -1, marker_share = -1, marker_existing = -1;

static void admit_marker(void)
{
    marker_local = openat(directories[1].fd, "local", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (marker_local < 0) refuse("/usr/local", "marker ancestor is unavailable");
    inspect_directory(marker_local, "/usr/local", true, false);
    marker_share = openat(marker_local, "share", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (marker_share < 0) refuse("/usr/local/share", "marker ancestor is unavailable");
    inspect_directory(marker_share, "/usr/local/share", true, false);
    marker_existing = open_leaf(marker_share, marker_name, marker_name, false, true);
    if (marker_existing >= 0) {
        struct stat metadata;
        char contents[11];
        ssize_t count = read(marker_existing, contents, sizeof(contents));
        if (fstat(marker_existing, &metadata) || (metadata.st_mode & 07777) != 0644
            || count != 10 || memcmp(contents, "version=1\n", 10))
            refuse(marker_name, "existing build marker is invalid");
    }
}

static void publish_marker(void)
{
    if (marker_existing < 0) {
        int fd = openat(marker_share, marker_name,
            O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (fd < 0 || write(fd, "version=1\n", 10) != 10
            || fchown(fd, root_owner, root_group) || fchmod(fd, 0644)
            || fsync(fd) || fsync(marker_share))
            refuse(marker_name, "build marker publication failed");
        close(fd);
    } else close(marker_existing);
    close(marker_share);
    close(marker_local);
}

int main(int argc, char **argv)
{
    if (argc < 2 || (strcmp(argv[1], "normalize") && strcmp(argv[1], "check"))) return 64;
    normalize = !strcmp(argv[1], "normalize");
    bool require_all = false;
    for (int i = 2; i < argc; ++i) {
        if (!strcmp(argv[i], "--require-all")) require_all = true;
        else if (!strcmp(argv[i], "--bootstrap-root") && !normalize) bootstrap_root = true;
        else return 64;
    }
    const char *root = "/";
    root_owner = 0;
#ifdef NATIVE_ACTOR_TESTING
    // Production has no configurable root. The fixture changes only the
    // namespace entry descriptor and root UID to avoid touching host paths.
    root = getenv("NATIVE_ACTOR_TEST_ROOT");
    if (!root || !*root) return 64;
    root_owner = geteuid();
    root_group = getegid();
#endif
    if (normalize && geteuid() != root_owner) return 64;
    directories[0].fd = open(root, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (directories[0].fd < 0) refuse("/", "root descriptor is unavailable");
    size_t count = sizeof(directories) / sizeof(directories[0]);
    for (size_t i = 0; i < count; ++i) {
        if (i) {
            directories[i].fd = openat(directories[directories[i].parent].fd,
                directories[i].name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            if (directories[i].fd < 0) refuse(directory_paths[i], "ancestor is not a real accessible directory");
        }
        inspect_directory(directories[i].fd, directory_paths[i],
            normalize || (bootstrap_root && i <= 2), normalize && i > 0);
    }
    int php = open_leaf(directories[2].fd, "php85", "/usr/bin/php85", true, false);
    int actor[sizeof(actors) / sizeof(actors[0])];
    size_t actor_count = sizeof(actor) / sizeof(actor[0]);
    bool native = false;
    for (size_t i = 0; i < actor_count; ++i) {
        actor[i] = open_leaf(directories[actors[i].parent].fd, actors[i].name,
            actors[i].path, false, !require_all);
        native = native || (i && actor[i] >= 0);
    }
    // Never mark a partial set: startup would refuse the built image.
    for (size_t i = 0; native && i < actor_count; ++i)
        if (actor[i] < 0) refuse(actors[i].path, "native actor set is partial; startup requires every actor");
    if (normalize && native) admit_marker();
    if (normalize) {
        // All leaves and ancestors were admitted before the first mutation.
        for (size_t i = 1; i < count; ++i) set_mode(directories[i].fd, directory_paths[i], 0755);
        set_mode(php, "/usr/bin/php85", 0755);
        for (size_t i = 0; i < actor_count; ++i)
            if (actor[i] >= 0) set_mode(actor[i], actors[i].path, 0644);
        if (native) publish_marker();
    }
    for (size_t i = 0; i < actor_count; ++i)
        if (actor[i] >= 0) close(actor[i]);
    close(php);
    for (size_t i = 0; i < count; ++i) close(directories[i].fd);
    puts("native-actor-paths: fixed PHP and actor paths admitted");
    return 0;
}
