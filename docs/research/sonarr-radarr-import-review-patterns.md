# Sonarr and Radarr Import-Review Patterns

Research date: 2026-08-01. This is an AFK research note for issue #59. Sources
are Servarr's official wiki and the current `develop` branches of the official
Sonarr and Radarr repositories.

## Findings

### Download and automatic import

Sonarr and Radarr poll the configured download-client category; their queue is
a view of that client rather than application-owned persisted state. Completed
downloads enter an automatic import pipeline. The pipeline uses the download
title and grab history to establish the intended series/movie, then scans the
output path and makes a decision per media file. A target-title mismatch,
unparseable download, or non-interactive ID-only match blocks automatic import
instead of silently accepting it. [Sonarr activity](https://wiki.servarr.com/sonarr/activity),
[Radarr activity](https://wiki.servarr.com/radarr/activity),
[Sonarr source](https://github.com/Sonarr/Sonarr/blob/develop/src/NzbDrone.Core/Download/CompletedDownloadService.cs),
[Radarr source](https://github.com/Radarr/Radarr/blob/develop/src/NzbDrone.Core/Download/CompletedDownloadService.cs)

`ImportBlocked` is a durable review state while the client item remains
tracked. On the first transition only, each application emits
`ManualInteractionRequiredEvent`; normal completion emits a distinct
`DownloadCompletedEvent`. This separates "needs human action" from "files are
available." [Sonarr source](https://github.com/Sonarr/Sonarr/blob/develop/src/NzbDrone.Core/Download/CompletedDownloadService.cs),
[Radarr source](https://github.com/Radarr/Radarr/blob/develop/src/NzbDrone.Core/Download/CompletedDownloadService.cs),
[Sonarr event](https://github.com/Sonarr/Sonarr/blob/develop/src/NzbDrone.Core/Download/ManualInteractionRequiredEvent.cs),
[Radarr event](https://github.com/Radarr/Radarr/blob/develop/src/NzbDrone.Core/Download/ManualInteractionRequiredEvent.cs)

Automatic completion is target-scoped: Sonarr requires the expected episode
count to be imported (or already present in history), while Radarr accepts an
imported movie. Failed or incomplete results return to pending or blocked with
per-file error messages. [Sonarr source](https://github.com/Sonarr/Sonarr/blob/develop/src/NzbDrone.Core/Download/CompletedDownloadService.cs),
[Radarr source](https://github.com/Radarr/Radarr/blob/develop/src/NzbDrone.Core/Download/CompletedDownloadService.cs)

### Manual import and mapping

The queue has a Manual Import action, and the broader Manual/Interactive Import
flow presents candidate files plus rejection information. The user can select
the library target and override parsed attributes before executing an import.
The services re-augment the candidate from filename, folder, and tracked
download evidence, then deliberately reapply the user's target and choices.
[Sonarr activity](https://wiki.servarr.com/sonarr/activity),
[Radarr activity](https://wiki.servarr.com/radarr/activity),
[Sonarr manual-import source](https://github.com/Sonarr/Sonarr/blob/develop/src/NzbDrone.Core/MediaFiles/EpisodeImport/Manual/ManualImportService.cs),
[Radarr manual-import source](https://github.com/Radarr/Radarr/blob/develop/src/NzbDrone.Core/MediaFiles/MovieImport/Manual/ManualImportService.cs)

This is a per-file mapping model, not a blanket folder acceptance model. The
automatic import services enumerate supported files, request one decision per
file, import only approved decisions, and preserve a reason for unsafe or
unsupported candidates. [Sonarr source](https://github.com/Sonarr/Sonarr/blob/develop/src/NzbDrone.Core/MediaFiles/DownloadedEpisodesImportService.cs),
[Radarr source](https://github.com/Radarr/Radarr/blob/develop/src/NzbDrone.Core/MediaFiles/DownloadedMovieImportService.cs)

### Retained downloads

By default, completed torrent data remains in the download directory so the
client can seed; import hardlinks where possible and otherwise copies. Optional
"Remove Completed" asks the client to remove stopped, seed-complete torrents.
[Sonarr FAQ](https://wiki.servarr.com/sonarr/faq#why-are-there-two-files-why-is-there-a-file-left-in-downloads),
[Radarr FAQ](https://wiki.servarr.com/radarr/faq#why-are-there-two-files-why-is-there-a-file-left-in-downloads)

At filesystem level, the importers delete a source folder only after a move
when it contains no non-sample media and no large RARs. Thus residual useful
files are not deleted merely because some candidates imported. [Sonarr source](https://github.com/Sonarr/Sonarr/blob/develop/src/NzbDrone.Core/MediaFiles/DownloadedEpisodesImportService.cs),
[Radarr source](https://github.com/Radarr/Radarr/blob/develop/src/NzbDrone.Core/MediaFiles/DownloadedMovieImportService.cs)

**Limitation:** no primary source found that documents a first-class workflow
for reusing one partly imported torrent as a later, separately tracked release
for another Sonarr series or Radarr movie. The sources support physical
retention and manual per-file import, but do not establish source-release
identity as a reusable many-target entity.

### Operational notifications

Connections support multiple transports, including email, Apprise, custom
scripts, Discord, webhooks, and media-server integrations. The documented
success triggers distinguish grab, individual file import/upgrade, and, in
Sonarr, full import completion. [Sonarr settings](https://wiki.servarr.com/sonarr/settings#connection-triggers),
[Radarr settings](https://wiki.servarr.com/radarr/settings#connection-triggers),
[Sonarr supported connections](https://wiki.servarr.com/sonarr/supported#notifications),
[Radarr supported connections](https://wiki.servarr.com/radarr/supported#notifications)

The source additionally routes manual-interaction-required and health-failure/
restoration events through enabled connections, records delivery success or
failure, and processes provider queues after relevant operations. [Sonarr source](https://github.com/Sonarr/Sonarr/blob/develop/src/NzbDrone.Core/Notifications/NotificationService.cs),
[Radarr source](https://github.com/Radarr/Radarr/blob/develop/src/NzbDrone.Core/Notifications/NotificationService.cs)

**Limitation:** the reviewed official wiki trigger lists do not explicitly
document a user-facing notification for every blocked import. The source proves
that an enabled manual-interaction connection receives such an event, but this
note did not verify each provider's payload or retry contract.

## Applicability to Shelfmark

Shelfmark defines a Book as catalog identity, Files as per-artifact rows, and a
release as the File rows sharing a task ID. Its current collection research
identifies the opposite of Servarr's per-file decision boundary: every supported
file in a selected torrent inherits one Book without per-file evidence.
[Shelfmark context](../../CONTEXT.md),
[collection matching research](collection-torrent-book-matching.md)

Adopt the pattern, not the media-specific parser:

1. Add an explicit reviewable outcome between scan and transfer: each candidate
   needs evidence, a proposed Book (or no proposal), and a rejection/no-match
   reason. Do not treat selection of a search result as proof for every member
   of a collection.
2. Make automatic completion Book-scoped and affirmative. A collection with
   zero proven files should become a visible "manual review/no match" outcome,
   not a successful release that creates wrong File rows.
3. Preserve the physical torrent unless an explicit retention policy permits
   removal. This agrees with the existing recommendation that unmatched members
   remain available for a later Book selection, but Shelfmark needs a separate
   source-release identity so that later selection does not collide with the
   earlier Book activity.
4. Notify operational recipients on actionable review failure and on completed
   availability as separate events. Follow Servarr's distinction between
   per-file import and release-level completion; retain Shelfmark's existing
   distinction between administrator operational alerts and personal
   notifications.

Servarr's automatic mapping relies on the rich, structured episode/movie
metadata encoded in release names and grab history. It is not evidence that
fuzzy title matching is safe for books. For Shelfmark, retain the existing
report's stricter EPUB/ISBN and exact title-author proposal rather than copying
Servarr's filename parsing policy. [collection matching research](collection-torrent-book-matching.md)
