# Production source evidence

This branch is the immutable public evidence layer for official source files.
House index ZIPs and PTR PDFs are stored under `house_clerk/` by SHA-256 together with retrieval metadata.
Normalized frontend data remains on `main`; operational checkpoints remain on `state`.
Credentials and unreviewed extracted rows are never stored here.
