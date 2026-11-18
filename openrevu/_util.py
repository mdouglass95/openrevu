import functools


def mutates(fn):
    """Make a mutating Document method one undo step (see Document._operation)."""
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        with self._operation(fn.__name__):
            return fn(self, *a, **kw)
    return wrapper
