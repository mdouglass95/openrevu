import contextlib
import functools
import os


def mutates(fn):
    """Make a mutating Document method one undo step (see Document._operation)."""
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        with self._operation(fn.__name__):
            return fn(self, *a, **kw)
    return wrapper


def replace_file(path: str, write) -> None:
    """Replace `path` in one step: `write(tmp)` writes the new content to a temporary file next to it, which then
    replaces `path`. A crash never leaves half a file. On failure the temporary file is removed and the error raised."""
    tmp = path + ".tmp"
    try:
        write(tmp)
        try:
            os.replace(tmp, path)
        except OSError as e:
            raise OSError(e.errno, f"Could not replace the file; is it open in another program? ({e.strerror})",
                          path) from e
    except Exception:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
