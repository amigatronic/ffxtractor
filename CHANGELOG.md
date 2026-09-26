# Changelog

## v7

- Hide `time=N/A` lines from the GUI log to reduce visual noise.
- Keep progress reporting active through a size-based fallback when FFmpeg reports `time=N/A`.
- Fix batch processing so per-stream checkbox deselections are respected.
- Use output stream indexes for `-c:N` codec arguments instead of input stream indexes.
- Add a `Flags` column for forced/default stream dispositions.
- Normalize application paths to forward slashes before passing them to FFmpeg.

## Earlier versions

Earlier release history is not present in the supplied source file.

Additional historical entries should be added here when the corresponding versions are documented.
