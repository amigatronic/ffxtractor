# Contributing

Thank you for contributing to FFxtractor.

## Development environment

Use Windows 10 with Python 3.9 or newer.

Install the project dependency:

```text
pip install -r requirements.txt
```

FFmpeg and FFprobe are required to run the application.

## Before submitting a change

Run the basic syntax check:

```text
python -m py_compile ffxtractor.py
```

Then manually verify the affected GUI workflow.

For changes involving FFmpeg command construction, test at least:

- single-file processing;
- batch processing;
- Copy;
- Convert;
- Discard;
- checked and unchecked streams;
- multiple output streams;
- existing output files;
- FFmpeg failure handling.

## Code style

Keep changes focused and avoid unnecessary architectural rewrites.

User-facing text, comments, logs, variable names, and documentation should remain in English.

Do not introduce additional runtime dependencies unless they provide a clear benefit.

## Pull requests

A pull request should describe:

- what changed;
- why it changed;
- how it was tested;
- any FFmpeg/container limitations that remain.

Do not include generated media files or private media in the repository.
