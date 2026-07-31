# Admin release deletion is destructive and global

**Status:** accepted

This supersedes ADR 0002's per-user release-unlink decision.

A library administrator may delete a completed release. Release deletion is
release-atomic in the successful case: it removes every release artifact from
disk, removes all `user_downloads` links, and clears `book_id` and
`download_path` on every `download_history` row with the release's `task_id`.
The retained rows preserve an audit trail but no longer represent files or a
Book association.

Non-administrators may download a release when they have library access, but
cannot delete it. In-flight releases cannot be deleted because finalization
could otherwise recreate artifacts after deletion.

If an artifact cannot be deleted after earlier artifacts have been removed,
the successfully removed artifacts are detached before the error is returned.
The remaining artifacts stay attached so an administrator can retry deletion.

## Consequences

- Release deletion removes files for every user, rather than hiding them for
  one user.
- A later download creates a new release and new file links for the Book.
- Filesystem deletion is not transactionally atomic; failure is recoverable
  without advertising an already removed file.
