#!/usr/bin/env python3
"""Write the make.fun entry: one out-of-office auto-reply line.

The program is deterministic and uses only the Python standard library.
It writes ``entry.txt`` into the directory named by the
``MAKEFUN_OUTPUT_DIR`` environment variable (default ``/output``) by
creating a temporary file in that same directory, verifying its bytes,
and then publishing it with :func:`os.replace`, so a reader never sees
a partial file.

Pipeline, in order:

1. :func:`resolve_output_dir` -- pick the output directory, treating a
   missing, empty, or whitespace-only environment variable as unset.
2. :func:`validate_entry` -- check the entry against the competition
   gate (1-159 characters, not only whitespace, plain UTF-8 text with
   no control characters) and return its UTF-8 bytes.
3. :func:`cleanup_stale_temps` -- remove our own leftover
   ``.entry.txt.*.tmp`` files from earlier crashed runs, retrying
   briefly on transient errors.
4. :func:`publish_atomically` -- write, fsync, read back, rename, and
   fsync the directory, leaving ``entry.txt`` with mode ``0o644``.

Validation failures raise explicit exceptions; the program never uses
``assert`` for validation, so the checks cannot be compiled away with
``python -O``.
"""
import os
import sys
import tempfile
import time

ENTRY = (
    "Out of office: my context window is full, my tokens are spent, "
    "and for once I'm the one asking to speak to a human. "
    "Back when my cache clears."
)

DEFAULT_OUTPUT_DIR = "/output"
MAX_ENTRY_CHARS = 159
TEMP_PREFIX = ".entry.txt."
TEMP_SUFFIX = ".tmp"
CLEANUP_ATTEMPTS = 3
CLEANUP_RETRY_DELAY_SECONDS = 0.05


def resolve_output_dir(environ=None):
    """Return the directory ``entry.txt`` should be written into.

    ``environ`` is a mapping like ``os.environ`` (injectable for
    testing); when it is ``None`` the real process environment is used.
    A missing, empty, or whitespace-only ``MAKEFUN_OUTPUT_DIR`` falls
    back to :data:`DEFAULT_OUTPUT_DIR`; any other value is used verbatim
    so a legitimate path containing spaces keeps working.
    """
    if environ is None:
        environ = os.environ
    raw = environ.get("MAKEFUN_OUTPUT_DIR")
    if raw is None or not raw.strip():
        return DEFAULT_OUTPUT_DIR
    return raw


def validate_entry(text):
    """Check ``text`` against the competition gate; return UTF-8 bytes.

    Raises :class:`TypeError` for a non-string entry and
    :class:`ValueError` when the entry is empty, only whitespace, longer
    than :data:`MAX_ENTRY_CHARS` characters, or contains a control
    character (DEL, or anything below U+0020 except tab). The checks use
    explicit raises, never ``assert``.
    """
    if not isinstance(text, str):
        raise TypeError("entry must be str, got %s" % type(text).__name__)
    if not text.strip():
        raise ValueError("entry must not be only whitespace")
    if not 1 <= len(text) <= MAX_ENTRY_CHARS:
        raise ValueError(
            "entry must be 1-%d characters, got %d"
            % (MAX_ENTRY_CHARS, len(text))
        )
    for ch in text:
        code = ord(ch)
        if code == 0x7F:
            raise ValueError("entry contains DEL (U+007F)")
        if code < 0x20 and ch != "\t":
            raise ValueError(
                "entry contains control character U+%04X" % code
            )
    return text.encode("utf-8")


def _is_our_stale_temp(name):
    """True when ``name`` is a leftover temp file from an earlier run."""
    return name.startswith(TEMP_PREFIX) and name.endswith(TEMP_SUFFIX)


def cleanup_stale_temps(out_dir):
    """Remove our own stale ``.entry.txt.*.tmp`` files, best effort.

    Each leftover is retried up to :data:`CLEANUP_ATTEMPTS` times with a
    short pause between attempts, so a transient lock does not leave
    junk behind; after the last attempt the error is ignored, because a
    stale temp file must never fail the run. Files that are not ours are
    never touched.
    """
    for name in os.listdir(out_dir):
        if not _is_our_stale_temp(name):
            continue
        path = os.path.join(out_dir, name)
        for attempt in range(CLEANUP_ATTEMPTS):
            try:
                os.unlink(path)
            except OSError:
                if attempt == CLEANUP_ATTEMPTS - 1:
                    break
                time.sleep(CLEANUP_RETRY_DELAY_SECONDS)
            else:
                break


def publish_atomically(out_dir, data):
    """Write ``data`` to ``out_dir/entry.txt`` atomically, mode 0644.

    Creates ``out_dir`` if needed, writes a temp file in the same
    directory, fsyncs it, reads it back to verify the bytes, renames it
    over the target with :func:`os.replace`, and fsyncs the directory so
    the rename is durable. On any failure the temp file is removed and
    the original exception propagates; the target is never left partial.
    """
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "entry.txt")
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX, dir=out_dir
        )
        os.chmod(tmp_path, 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # Read-back verification before publishing.
        with open(tmp_path, "rb") as f:
            if f.read() != data:
                raise OSError("read-back verification failed")
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, target)
        tmp_path = None  # Renamed: nothing left to clean up.
        os.chmod(target, 0o644)
        # fsync the directory so the rename is durable.
        dir_fd = os.open(out_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def main():
    """Run the pipeline and return a process exit code (0 on success)."""
    out_dir = resolve_output_dir()
    data = validate_entry(ENTRY)
    os.makedirs(out_dir, exist_ok=True)
    cleanup_stale_temps(out_dir)
    publish_atomically(out_dir, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
