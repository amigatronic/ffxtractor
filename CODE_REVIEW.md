# Code Review — FFxtractor

## Summary

The supplied `ffxtractor.py` is syntactically valid Python and provides a coherent PyQt5 GUI around FFmpeg/FFprobe.

The current implementation is suitable as a small standalone utility, but there are a few functional limitations that should be addressed before considering the application fully robust for arbitrary batch media sets.

## Verified

- Python compilation succeeds.
- FFmpeg and FFprobe paths are managed centrally through `FFmpegManager`.
- FFprobe JSON data is used to populate the stream table.
- Single-file processing runs in a `QThread`.
- Batch processing runs in a dedicated `QThread`.
- Stream mapping uses the original input stream index with `-map 0:<index>`.
- Codec selection uses the output stream counter with `-c:<output_index>`.
- Unchecked streams are explicitly converted to `Discard` in the rule extraction logic.
- `time=N/A` log lines are filtered while still allowing the size-based progress fallback.

## Findings

### 1. Batch cancellation is reported as completion

`BatchWorker` emits `all_finished` even when `_stop_requested` is true. `MainWindow._on_batch_finished()` always presents a `Batch completed` message.

As a result, a user cancellation can be reported using the same completion dialog as a normal batch.

**Recommendation:** add a dedicated cancelled state/result and display a cancellation summary.

### 2. Batch rules are tied to stream index and codec type

Batch rules are stored as:

```text
(stream_index, codec_type, action)
```

A rule is applied only when both the index and codec type match.

This is predictable, but it means a batch containing files with different stream layouts can produce different results from what the user intended.

**Recommendation:** for a future version, consider matching streams using stable metadata such as codec type, language, disposition, title, and/or an explicit rule editor.

### 3. Unmatched batch streams default to Copy

The batch command builder starts each stream with:

```text
action = "Copy"
```

Therefore, a stream that does not match any rule is copied instead of discarded.

This should be clearly communicated in the UI if batch mode is intended for heterogeneous media collections.

### 4. Progress fallback is approximate

The fallback compares FFmpeg's output size against the input file size.

This is useful when `time=N/A` is reported, but it cannot represent real transcoding progress reliably because output size is not proportional to elapsed processing time.

The current implementation correctly treats this as a fallback, but a future implementation could use FFmpeg's machine-readable progress interface.

### 5. No explicit empty-output validation

The user can potentially discard every stream.

FFmpeg will then determine how to handle the resulting command, which may produce an error.

**Recommendation:** validate that at least one output stream remains before starting the operation and show a direct GUI message.

### 6. FFmpeg executable validation checks execution, not return code

`FFmpegManager._is_valid()` invokes:

```text
ffmpeg_path -version
```

but does not inspect the subprocess return code.

A future improvement could require a successful return code and optionally verify that the executable identifies itself as FFmpeg/FFprobe.

### 7. Codec/container compatibility is not validated by the GUI

The application allows combinations that FFmpeg may reject.

For example, subtitle and codec compatibility varies between containers.

This is acceptable for a thin FFmpeg wrapper, but the UI could eventually filter incompatible codec choices or show a clearer validation error.

## Architecture

The source is intentionally a single file.

For the current project size this is manageable, but the main window currently owns:

- UI construction;
- FFmpeg configuration;
- FFprobe handling;
- stream-rule extraction;
- command construction;
- batch job preparation;
- worker lifecycle management.

If the application grows substantially, these responsibilities would be natural candidates for separate modules.

No architectural rewrite is required for the current GitHub publication.

## Suggested priority

### High priority

- Correct batch cancellation reporting.
- Validate that at least one output stream is selected.
- Make the batch stream-matching limitation explicit in the UI.

### Medium priority

- Improve progress reporting using FFmpeg's machine-readable progress output.
- Validate FFmpeg/FFprobe return codes.
- Add automated tests for command generation and batch rule matching.

### Low priority

- Split the monolithic source into modules once the feature set grows.
- Add more metadata-aware stream matching.
- Add richer container/codec compatibility handling.

## Conclusion

The current source is structurally usable and compiles successfully. The main risks are not syntax errors but edge cases around heterogeneous batch inputs, cancellation state, and progress estimation.
