"""Ownership of nested TestCase fixtures, including incomplete setup."""
from contextlib import ExitStack


def setup_fixture(parent, child):
    """Set up child and give parent its cleanup only after setup succeeds."""
    def cleanup():
        # doCleanups runs every callback even when one fails, but reports failure
        # as a boolean rather than raising it to the owning fixture.
        if not child.doCleanups():
            raise RuntimeError('{} fixture cleanup failed'.format(type(child).__name__))

    with ExitStack() as pending:
        pending.callback(cleanup)
        child.setUp()
        parent.addCleanup(pending.pop_all().close)
    return child
