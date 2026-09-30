# House source-to-review fixed handoff

The House source workflow writes its existing official `evidence` and public
`state` branches under the shared source-writer lock. After both steps finish,
it records their exact commit SHAs, the executing code SHA, and its run ID in
`house-source-handoff/v1`, uploaded as an artifact tied to that source run.

For a successful `workflow_run` from the repository's `code` branch, House
review downloads only that run's artifact, checks the run ID and source code
SHA against the GitHub event, validates the pinned SHA formats, and checks out
those exact evidence and state commits. A missing or mismatched artifact fails
the review job before parsing or writing `review`. Manual review runs retain
their existing latest-ref recovery behavior. The House source workflow now
accepts writes only when dispatched from `code`.

This fixes the prior event path in which a later source writer could advance
`evidence` or `state` between House source completion and House review start.
The review branch remains a single writer under its existing lock; no new
schedule, data source, production candidate rule, or publication writer was
added. The handoff artifact is retained for seven days; durable evidence and
state remain in their existing Git branches.

Remote source-to-review execution still needs validation after this workflow
change is merged. A successful offline or PR check alone does not prove the
event artifact and pinned checkout work on GitHub Actions.
