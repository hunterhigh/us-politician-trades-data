# Production source evidence

This branch is the immutable public evidence layer for official source files.
House source files are stored under `house_clerk/`; Senate source files are stored under `senate_efd/`.
Original bytes are addressed by SHA-256 together with retrieval metadata.
Normalized frontend data remains on `main`; operational checkpoints remain on `state`.
Credentials and unreviewed extracted rows are never stored here.
