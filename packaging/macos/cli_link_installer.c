// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 wardmos
// Publish ShotQuill CLI symlinks without replacing any directory entry.

#define _DARWIN_C_SOURCE 1
#ifdef __linux__
#define _GNU_SOURCE 1
#endif

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#ifdef __APPLE__
#include <sys/acl.h>
#endif

#ifdef __linux__
#include <linux/fs.h>
#include <sys/syscall.h>
#endif

#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif

static const char *const kExpectedTarget =
    "/Applications/ShotQuill.app/Contents/MacOS/ShotQuill";
static const char *const kCommands[] = {"shotquill", "squill"};

struct Entry {
    const char *name;
    bool existed;
    bool created;
    dev_t device;
    ino_t inode;
};

static int remove_stage_directory(
    int parent_fd,
    int stage_fd,
    const char *stage_name
);
static int verify_directory_empty(int fd);

static void report_errno(const char *message, const char *path) {
    fprintf(stderr, "shotquill-cli-install: %s %s: %s\n", message, path, strerror(errno));
}

static int rename_exclusive(
    int source_fd,
    const char *source,
    int destination_fd,
    const char *destination
) {
#ifdef __APPLE__
    return renameatx_np(source_fd, source, destination_fd, destination, RENAME_EXCL);
#elif defined(__linux__)
    return (int)syscall(
        SYS_renameat2,
        source_fd,
        source,
        destination_fd,
        destination,
        RENAME_NOREPLACE
    );
#else
    errno = ENOTSUP;
    return -1;
#endif
}

static int open_physical_directory_at(int parent_fd, const char *name, bool create) {
    int fd = openat(parent_fd, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0 && create && errno == ENOENT) {
        if (mkdirat(parent_fd, name, 0755) < 0 && errno != EEXIST) {
            return -1;
        }
        fd = openat(parent_fd, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    }
    if (fd < 0) {
        return -1;
    }
    struct stat metadata;
    if (fstat(fd, &metadata) < 0 || !S_ISDIR(metadata.st_mode)) {
        int saved_errno = errno == 0 ? ENOTDIR : errno;
        close(fd);
        errno = saved_errno;
        return -1;
    }
    return fd;
}

static int verify_directory_binding(int parent_fd, const char *name, int child_fd) {
    struct stat opened;
    struct stat named;
    if (fstat(child_fd, &opened) < 0
        || fstatat(parent_fd, name, &named, AT_SYMLINK_NOFOLLOW) < 0) {
        return -1;
    }
    if (!S_ISDIR(opened.st_mode) || !S_ISDIR(named.st_mode)
        || opened.st_dev != named.st_dev || opened.st_ino != named.st_ino) {
        errno = ESTALE;
        return -1;
    }
    return 0;
}

static int verify_directory_chain(int root_fd, int usr_fd, int local_fd, int bin_fd) {
    if (verify_directory_binding(root_fd, "usr", usr_fd) < 0
        || verify_directory_binding(usr_fd, "local", local_fd) < 0
        || verify_directory_binding(local_fd, "bin", bin_fd) < 0) {
        return -1;
    }
    return 0;
}

#ifdef __APPLE__
static int has_extended_acl(int fd, bool *has_acl) {
    acl_t acl = acl_get_fd_np(fd, ACL_TYPE_EXTENDED);
    if (acl == NULL) {
        // Darwin may report ENOENT when an existing object has no extended ACL.
        if (errno == ENOENT) {
            *has_acl = false;
            return 0;
        }
        return -1;
    }
    acl_entry_t entry;
    int result = acl_get_entry(acl, ACL_FIRST_ENTRY, &entry);
    int saved_errno = errno;
    if (acl_free(acl) < 0) {
        return -1;
    }
    if (result < 0) {
        errno = saved_errno;
        return -1;
    }
    *has_acl = result == 1;
    return 0;
}

static int replace_with_empty_stage_acl(int fd) {
    acl_t empty_acl = acl_init(0);
    if (empty_acl == NULL) {
        return -1;
    }
    int result = acl_set_fd_np(fd, empty_acl, ACL_TYPE_EXTENDED);
    int saved_errno = errno;
    if (acl_free(empty_acl) < 0 && result == 0) {
        return -1;
    }
    if (result < 0) {
        errno = saved_errno;
        return -1;
    }
    return 0;
}
#endif

static int harden_private_stage(int fd) {
#ifdef __APPLE__
    bool has_acl;
    // An inherited ACE can grant ownership or permission changes despite 0700.
    // Revoke it before normalizing BSD ownership/mode, then revoke again in
    // case a previously authorized operation raced with the first update.
    if (replace_with_empty_stage_acl(fd) < 0) {
        return -1;
    }
    if (fchown(fd, 0, (gid_t)-1) < 0 || fchmod(fd, 0700) < 0) {
        return -1;
    }
    if (replace_with_empty_stage_acl(fd) < 0) {
        return -1;
    }
#else
    (void)fd;
#endif

    struct stat metadata;
    if (fstat(fd, &metadata) < 0) {
        return -1;
    }
    if (!S_ISDIR(metadata.st_mode) || metadata.st_uid != 0
        || (metadata.st_mode & 07777) != 0700) {
        errno = EPERM;
        return -1;
    }

#ifdef __APPLE__
    if (has_extended_acl(fd, &has_acl) < 0) {
        return -1;
    }
    if (has_acl) {
        errno = EPERM;
        return -1;
    }
#endif
    return verify_directory_empty(fd);
}

static int verify_sticky_temp_directory(int fd) {
    struct stat metadata;
    if (fstat(fd, &metadata) < 0) {
        return -1;
    }
    if (!S_ISDIR(metadata.st_mode) || metadata.st_uid != 0
        || (metadata.st_mode & 07777) != 01777) {
        errno = EPERM;
        return -1;
    }
#ifdef __APPLE__
    bool extended_acl;
    if (has_extended_acl(fd, &extended_acl) < 0) {
        return -1;
    }
    if (extended_acl) {
        errno = EPERM;
        return -1;
    }
#endif
    return 0;
}

static int verify_same_filesystem(int first_fd, int second_fd) {
    struct stat first;
    struct stat second;
    if (fstat(first_fd, &first) < 0 || fstat(second_fd, &second) < 0) {
        return -1;
    }
    if (first.st_dev != second.st_dev) {
        errno = EXDEV;
        return -1;
    }
    return 0;
}

static int verify_directory_empty(int fd) {
    int scan_fd = dup(fd);
    if (scan_fd < 0) {
        return -1;
    }
    DIR *directory = fdopendir(scan_fd);
    if (directory == NULL) {
        int saved_errno = errno;
        close(scan_fd);
        errno = saved_errno;
        return -1;
    }

    bool unexpected = false;
    errno = 0;
    struct dirent *entry;
    while ((entry = readdir(directory)) != NULL) {
        if (strcmp(entry->d_name, ".") != 0 && strcmp(entry->d_name, "..") != 0) {
            unexpected = true;
            break;
        }
    }
    int saved_errno = errno;
    if (closedir(directory) < 0 && saved_errno == 0) {
        saved_errno = errno;
    }
    if (unexpected) {
        errno = ENOTEMPTY;
        return -1;
    }
    if (saved_errno != 0) {
        errno = saved_errno;
        return -1;
    }
    return 0;
}

static bool link_target_matches_at(int directory_fd, const char *name) {
    const size_t expected_length = strlen(kExpectedTarget);
    char target[256];
    ssize_t length = readlinkat(directory_fd, name, target, sizeof(target));
    return length == (ssize_t)expected_length
        && memcmp(target, kExpectedTarget, expected_length) == 0;
}

// 0 means absent, 1 means the exact ShotQuill symlink, -1 means collision/error.
static int inspect_entry(int directory_fd, const char *name, struct stat *metadata) {
    if (fstatat(directory_fd, name, metadata, AT_SYMLINK_NOFOLLOW) < 0) {
        if (errno == ENOENT) {
            return 0;
        }
        return -1;
    }
    if (!S_ISLNK(metadata->st_mode) || !link_target_matches_at(directory_fd, name)) {
        errno = EEXIST;
        return -1;
    }
    return 1;
}

static bool identity_matches_at(
    int directory_fd,
    const char *name,
    dev_t expected_device,
    ino_t expected_inode
) {
    struct stat metadata;
    return fstatat(directory_fd, name, &metadata, AT_SYMLINK_NOFOLLOW) == 0
        && S_ISLNK(metadata.st_mode)
        && metadata.st_dev == expected_device
        && metadata.st_ino == expected_inode
        && link_target_matches_at(directory_fd, name);
}

static int create_private_stage(int parent_fd, char *name, size_t name_size) {
    for (unsigned int attempt = 0; attempt < 128; ++attempt) {
        uint32_t nonce = arc4random();
        int written = snprintf(
            name,
            name_size,
            ".shotquill-cli-install.%ld.%08x",
            (long)getpid(),
            nonce
        );
        if (written < 0 || (size_t)written >= name_size) {
            errno = ENAMETOOLONG;
            return -1;
        }
        if (mkdirat(parent_fd, name, 0700) == 0) {
            int stage_fd = openat(
                parent_fd,
                name,
                O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC
            );
            if (stage_fd < 0) {
                return -1;
            }
            if (harden_private_stage(stage_fd) < 0) {
                int saved_errno = errno == 0 ? EPERM : errno;
                (void)remove_stage_directory(parent_fd, stage_fd, name);
                close(stage_fd);
                errno = saved_errno;
                return -1;
            }
            return stage_fd;
        }
        if (errno != EEXIST) {
            return -1;
        }
    }
    errno = EEXIST;
    return -1;
}

static int remove_stage_directory(
    int parent_fd,
    int stage_fd,
    const char *stage_name
) {
    struct stat opened;
    struct stat named;
    if (fstat(stage_fd, &opened) < 0
        || fstatat(parent_fd, stage_name, &named, AT_SYMLINK_NOFOLLOW) < 0
        || !S_ISDIR(named.st_mode)
        || opened.st_dev != named.st_dev
        || opened.st_ino != named.st_ino) {
        errno = ESTALE;
        return -1;
    }
    // parent_fd is /private/tmp, verified root-owned, sticky, and ACL-free.
    // Non-root users therefore cannot replace this root-owned entry between
    // the identity check and removal.
    return unlinkat(parent_fd, stage_name, AT_REMOVEDIR);
}

static int rollback_entry(
    int bin_fd,
    int stage_fd,
    struct Entry *entry
) {
    if (!entry->created) {
        return 0;
    }
    char rollback_name[128];
    for (unsigned int attempt = 0; attempt < 128; ++attempt) {
        int written = snprintf(
            rollback_name,
            sizeof(rollback_name),
            "rollback.%s.%08x",
            entry->name,
            arc4random()
        );
        if (written < 0 || (size_t)written >= sizeof(rollback_name)) {
            errno = ENAMETOOLONG;
            return -1;
        }
        if (rename_exclusive(bin_fd, entry->name, stage_fd, rollback_name) == 0) {
            break;
        }
        if (errno == ENOENT) {
            entry->created = false;
            return 0;
        }
        if (errno != EEXIST || attempt == 127) {
            return -1;
        }
    }

    if (identity_matches_at(stage_fd, rollback_name, entry->device, entry->inode)) {
        if (unlinkat(stage_fd, rollback_name, 0) < 0) {
            return -1;
        }
        entry->created = false;
        return 0;
    }

    // A replacement won the race. Put it back only if the public name is still
    // empty; otherwise leave both generations intact for manual inspection.
    if (rename_exclusive(stage_fd, rollback_name, bin_fd, entry->name) < 0) {
        fprintf(
            stderr,
            "shotquill-cli-install: preserving changed %s as private stage entry %s\n",
            entry->name,
            rollback_name
        );
    }
    errno = ESTALE;
    return -1;
}

static int rollback_created_entries(
    int bin_fd,
    int stage_fd,
    struct Entry entries[2]
) {
    int failed = 0;
    for (int index = 1; index >= 0; --index) {
        if (rollback_entry(bin_fd, stage_fd, &entries[index]) < 0) {
            failed = 1;
        }
    }
    return failed == 0 ? 0 : -1;
}

static int publish_entry(
    int bin_fd,
    int stage_fd,
    struct Entry *entry
) {
    if (symlinkat(kExpectedTarget, stage_fd, entry->name) < 0) {
        return -1;
    }
    struct stat staged;
    if (fstatat(stage_fd, entry->name, &staged, AT_SYMLINK_NOFOLLOW) < 0
        || !S_ISLNK(staged.st_mode)
        || !link_target_matches_at(stage_fd, entry->name)) {
        int saved_errno = errno == 0 ? ESTALE : errno;
        unlinkat(stage_fd, entry->name, 0);
        errno = saved_errno;
        return -1;
    }
    entry->device = staged.st_dev;
    entry->inode = staged.st_ino;
    if (rename_exclusive(stage_fd, entry->name, bin_fd, entry->name) < 0) {
        int saved_errno = errno;
        unlinkat(stage_fd, entry->name, 0);
        errno = saved_errno;
        return -1;
    }
    entry->created = true;
    if (!identity_matches_at(bin_fd, entry->name, entry->device, entry->inode)) {
        errno = ESTALE;
        return -1;
    }
    return 0;
}

int main(int argc, char **argv) {
    if (geteuid() != 0) {
        fprintf(stderr, "shotquill-cli-install: system installer privileges are required\n");
        return 1;
    }
    if (argc < 4 || strcmp(argv[3], "/") != 0) {
        fprintf(stderr, "shotquill-cli-install: only the system volume is supported\n");
        return 1;
    }
    umask(0022);

    int root_fd = open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (root_fd < 0) {
        report_errno("cannot open", "/");
        return 1;
    }
    int usr_fd = open_physical_directory_at(root_fd, "usr", false);
    int local_fd = usr_fd < 0 ? -1 : open_physical_directory_at(usr_fd, "local", true);
    int bin_fd = local_fd < 0 ? -1 : open_physical_directory_at(local_fd, "bin", true);
    if (usr_fd < 0 || local_fd < 0 || bin_fd < 0) {
        report_errno("cannot open or create physical directory", "/usr/local/bin");
        if (bin_fd >= 0) close(bin_fd);
        if (local_fd >= 0) close(local_fd);
        if (usr_fd >= 0) close(usr_fd);
        close(root_fd);
        return 1;
    }
    if (verify_directory_chain(root_fd, usr_fd, local_fd, bin_fd) < 0) {
        report_errno("directory changed while opening", "/usr/local/bin");
        close(bin_fd);
        close(local_fd);
        close(usr_fd);
        close(root_fd);
        return 1;
    }

    struct Entry entries[2] = {
        {.name = kCommands[0]},
        {.name = kCommands[1]},
    };
    bool needs_stage = false;
    for (size_t index = 0; index < 2; ++index) {
        struct stat metadata;
        int state = inspect_entry(bin_fd, entries[index].name, &metadata);
        if (state < 0) {
            report_errno("refusing to replace existing", entries[index].name);
            close(bin_fd);
            close(local_fd);
            close(usr_fd);
            close(root_fd);
            return 1;
        }
        entries[index].existed = state == 1;
        needs_stage = needs_stage || state == 0;
    }

    int private_fd = -1;
    int temp_fd = -1;
    char stage_name[128] = "";
    int stage_fd = -1;
    if (needs_stage) {
        private_fd = open_physical_directory_at(root_fd, "private", false);
        temp_fd = private_fd < 0
            ? -1
            : open_physical_directory_at(private_fd, "tmp", false);
        if (private_fd < 0 || temp_fd < 0
            || verify_directory_binding(root_fd, "private", private_fd) < 0
            || verify_directory_binding(private_fd, "tmp", temp_fd) < 0
            || verify_sticky_temp_directory(temp_fd) < 0
            || verify_same_filesystem(temp_fd, bin_fd) < 0) {
            report_errno("cannot use protected staging at", "/private/tmp");
            if (temp_fd >= 0) close(temp_fd);
            if (private_fd >= 0) close(private_fd);
            close(bin_fd);
            close(local_fd);
            close(usr_fd);
            close(root_fd);
            return 1;
        }
        stage_fd = create_private_stage(temp_fd, stage_name, sizeof(stage_name));
        if (stage_fd < 0) {
            report_errno("cannot create protected staging under", "/private/tmp");
            close(temp_fd);
            close(private_fd);
            close(bin_fd);
            close(local_fd);
            close(usr_fd);
            close(root_fd);
            return 1;
        }
    }

    int status = 0;
    for (size_t index = 0; index < 2; ++index) {
        if (!entries[index].existed && publish_entry(bin_fd, stage_fd, &entries[index]) < 0) {
            report_errno("cannot publish without replacing", entries[index].name);
            status = 1;
            break;
        }
    }
    for (size_t index = 0; status == 0 && index < 2; ++index) {
        if (entries[index].created
            && !identity_matches_at(
                bin_fd,
                entries[index].name,
                entries[index].device,
                entries[index].inode
            )) {
            fprintf(stderr, "shotquill-cli-install: %s changed during installation\n", entries[index].name);
            status = 1;
        } else if (entries[index].existed) {
            struct stat ignored;
            if (inspect_entry(bin_fd, entries[index].name, &ignored) != 1) {
                fprintf(stderr, "shotquill-cli-install: %s changed during installation\n", entries[index].name);
                status = 1;
            }
        }
    }

    if (status == 0 && verify_directory_chain(root_fd, usr_fd, local_fd, bin_fd) < 0) {
        report_errno("directory changed during installation", "/usr/local/bin");
        status = 1;
    }
    if (status == 0 && stage_fd >= 0
        && (verify_directory_binding(root_fd, "private", private_fd) < 0
            || verify_directory_binding(private_fd, "tmp", temp_fd) < 0
            || verify_sticky_temp_directory(temp_fd) < 0
            || verify_same_filesystem(temp_fd, bin_fd) < 0)) {
        report_errno("staging directory changed during installation", "/private/tmp");
        status = 1;
    }

    if (status == 0 && stage_fd >= 0
        && remove_stage_directory(temp_fd, stage_fd, stage_name) < 0) {
        report_errno("cannot remove private staging", stage_name);
        status = 1;
    }
    if (status != 0 && stage_fd >= 0) {
        if (rollback_created_entries(bin_fd, stage_fd, entries) < 0) {
            fprintf(stderr, "shotquill-cli-install: concurrent replacements were preserved\n");
        }
        if (remove_stage_directory(temp_fd, stage_fd, stage_name) < 0) {
            report_errno("private staging remains for inspection", stage_name);
        }
    }

    if (stage_fd >= 0) close(stage_fd);
    if (temp_fd >= 0) close(temp_fd);
    if (private_fd >= 0) close(private_fd);
    close(bin_fd);
    close(local_fd);
    close(usr_fd);
    close(root_fd);
    return status;
}
