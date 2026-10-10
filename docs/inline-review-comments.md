# Inline-only review publication

New review findings are published as GitLab positioned discussions or GitHub
pull-request review comments. General MR/PR comments are never a fallback.

## Placement

1. Use the finding's exact file, side, and line if present in the reviewed diff.
2. Otherwise use the first commentable new-side line in the same file (line 1
   when available). Deleted-only files use the old side.
3. For missing files, binary files, empty files, or unavailable patches, use the
   first eligible line in another changed text file.

Fallback comments explicitly identify the actual affected file or repository and
explain that their placement is a file-level anchor. The original AI file and
line are preserved separately from the remote anchor. Missing locations do not
cause findings to be dropped, and no additional paid AI pass is needed.

If the request has no commentable text lines, publication is **blocked**. A
line-specific comment is impossible in that case. It is not counted as posted.

## Revisions and retries

Every provider receives a fresh remote diff snapshot, which is saved with the
review. Publication checks its revision and diff against the current request.
Stale reviews require a fresh review. Legacy reviews without snapshots are also
blocked; existing remote IDs remain posted and are not reposted.

OpenCode workspaces are pinned to that run's immutable snapshot, not the current
branch tips. GitLab's `start_sha` identifies the recorded target commit and
`base_sha` identifies the recorded merge base; both remain in the saved snapshot
for inline positioning. The Review's `base_sha` preserves the API revision's
meaning rather than being overwritten with the workspace target.

The cache supplies existing commit objects or fetches missing objects by exact
SHA. An unavailable commit or inconsistent merge base fails explicitly; the
worker never substitutes newer branch tips. Snapshot metadata is persisted before
workspace preparation, so failed runs remain diagnosable. Each subsequent review
fetches a fresh API snapshot and can review newly pushed code. Target advancement
alone does not invalidate the recorded diff.

After deploying this code, restart the Celery review worker so it loads the updated
pipeline. No schema migration is needed for snapshot-driven workspaces. Run a new
review to retry a previously failed request; historical failures are not rewritten.

Stable markers recover remote posts after interrupted local persistence. Database
locks serialize publishers. Permanent provider rejections are blocked; transient
creation failures are recorded as failed and retried by the publication task,
without repeating the AI review. Successful posts survive partial failures.

## Operator workflow

The review detail page distinguishes AI completion from publication completion,
shows outstanding/blocked counts, and displays each finding's anchor or error.
The Django admin supports editing the finding's file, line, and side before
retrying the Review's **Post pending review comments** action. Manual locations
receive the same diff validation and fallback rules as AI locations.

Apply the schema before running updated workers:

```sh
python manage.py migrate
```

The migration preserves existing posted IDs/statuses. It does not manufacture
revision snapshots for older reviews. No live provider comments are created by
the automated tests.
