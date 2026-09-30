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

Remote validation passed on 2026-09-30. [Source run 36665821546](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/36665821546)
at `code=5fac3456effb5533b0f26c94fb975462e22889ca` succeeded and uploaded
`house-source-handoff-36665821546`. Its downloaded JSON pinned
`evidence=0bfde57073838652d58ed1fa814b42c49bf7bd5c` and
`state=402c5313429738b410aac2647fde85417211ebcc`. The automatic
[review run 36665903913](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/36665903913)
downloaded that exact artifact, passed the immutable handoff validation and
pinned checkout, then completed qualification and published unified candidate
`review=edd5f8081c3c068dbfc1350179cd00a21d5441a7`. Its candidate contains
392 people, 18,017 transactions, and 5,593 reported holdings. This validates
the event handoff, not a vNext ledger input cutover.
