"""Helpers to let you quickly define epoch / batching semantics.

Use these to wrap an existing iterator which yields items.
"""
import datetime
import heapq
from typing import Any, Callable, Iterable, Tuple


def single_batch(wrap_iter: Iterable) -> Iterable[Tuple[int, Any]]:
    """All input items are part of the same epoch.

    Use this for non-streaming-style batch processing.

    >>> list(single_batch(["a", "b", "c"]))
    [(0, 'a'), (0, 'b'), (0, 'c')]

    Args:

        wrap_iter: Existing input iterable of just items.

    Yields: Tuples of (epoch, item).
    """
    for item in wrap_iter:
        yield (0, item)


def tumbling_epoch(
    wrap_iter: Iterable,
    epoch_length: Any,
    time_getter: Callable[[Any], Any] = lambda _: datetime.datetime.now(),
    epoch_0_start_time: Any = None,
) -> Iterable[Tuple[int, Any]]:
    """All inputs within a tumbling window are part of the same epoch.

    The time of the first item will be used as start of the 0
    epoch. Out-of-order items will cause issues as Bytewax requires
    inputs to dataflows to be in epoch order. See `order()`.

    >>> items = [
    ...     {
    ...         "timestamp": datetime.datetime(2022, 2, 22, 1, 2, 3),
    ...         "value": "a",
    ...     },
    ...     {
    ...         "timestamp": datetime.datetime(2022, 2, 22, 1, 2, 4),
    ...         "value": "b",
    ...     },
    ...     {
    ...         "timestamp": datetime.datetime(2022, 2, 22, 1, 2, 8),
    ...         "value": "c",
    ...     },
    ... ]
    >>> [
    ...     (epoch, item["value"])
    ...     for epoch, item
    ...     in tumbling_epoch(
    ...         items,
    ...         datetime.timedelta(seconds=2),
    ...         lambda item: item["timestamp"],
    ...     )
    ... ]
    [(0, 'a'), (0, 'b'), (2, 'c')]

    By default, uses "ingestion time" and you don't need to specify a
    way to access the timestamp in each item.

    >>> import pytest; pytest.skip("Figure out sleep in test.")
    >>> items = [
    ...     "a", # sleep(4)
    ...     "b", # sleep(1)
    ...     "c",
    ... ]
    >>> list(tumbling_epoch(items, datetime.timedelta(seconds=2)))
    [(0, 'a'), (0, 'b'), (2, 'c')]

    Args:

        wrap_iter: Existing input iterable of just items.

        epoch_length: Length of each epoch window.

        time_getter: Function that returns a timestamp given an
            item. Defaults to current wall time.

        epoch_0_start_time: The timestamp that should correspond to
            the start of the 0th epoch. Otherwise defaults to the time
            found on the first item.

    Yields: Tuples of (epoch, item).
    """
    for item in wrap_iter:
        time = time_getter(item)

        if epoch_0_start_time is None:
            epoch_0_start_time = time
            epoch = 0
        else:
            epoch = int((time - epoch_0_start_time) / epoch_length)

        yield (epoch, item)


def fully_ordered(wrap_iter: Iterable) -> Iterable[Tuple[int, any]]:
    """Each input item increments the epoch.

    Be careful using this in high-volume streams with many workers, as
    the worker overhead goes up with finely granulated epochs.

    >>> list(fully_ordered(["a", "b", "c"]))
    [(0, 'a'), (1, 'b'), (2, 'c')]

    Args:

        wrap_iter: Existing input iterable of just items.

    Yields: Tuples of (epoch, item).
    """
    epoch = 0
    for item in wrap_iter:
        yield (epoch, item)
        epoch += 1


def sorted_window(
    wrap_iter: Iterable,
    window_length: Any,
    time_getter: Callable[[Any], Any],
    on_drop: Callable[[Any], None] = None,
) -> Iterable[Tuple[int, Any]]:
    """Sort a iterator to be increasing by some timestamp.

    To support a possibly infinite iterator, store a limited sorted
    buffer of items and only emit things downstream once a certain
    window of time has passed, as indicated by the timestamp on new
    items.

    New input items which are older than those already emitted will be
    dropped to maintain sorted output.

    The window length needs to be tuned for how "out of order" your
    input data is and how much data you're willing to drop: Already
    perfectly ordered input data can have a window of "0" and nothing
    will be dropped. Completely reversed input data needs a window
    that is the difference between the oldest and youngest timestamp
    to ensure nothing will be dropped.

    >>> items = [
    ...     {
    ...         "timestamp": datetime.datetime(2022, 2, 22, 1, 2, 4),
    ...         "value": "c",
    ...     },
    ...     {
    ...         "timestamp": datetime.datetime(2022, 2, 22, 1, 2, 3),
    ...         "value": "b",
    ...     },
    ...     {
    ...         "timestamp": datetime.datetime(2022, 2, 22, 1, 2, 0),
    ...         "value": "a",
    ...     },
    ... ]
    >>> list(
    ...     sorted_window(
    ...         items,
    ...         datetime.timedelta(seconds=2),
    ...         lambda item: item["timestamp"],
    ...     )
    ... )
    [
        {'timestamp': datetime.datetime(2022, 2, 22, 1, 2, 3), 'value': 'b'},
        {'timestamp': datetime.datetime(2022, 2, 22, 1, 2, 4), 'value': 'c'}
    ]

    Args:

        wrap_iter: Existing input iterable.

        window_length: Buffering duration. Values will be emitted once
            this amount of time has passed.

        time_getter: Function to call to produce a timestamp for each
            value.

        on_drop: Function to call with each dropped item. E.g. log or
            increment metrics on drop events to refine your window
            length.

    Yields: Values in increasing timestamp order.

    """
    sorted_buffer = []
    newest_time = None
    drop_older_than = None

    def is_too_late(time):
        return drop_older_than is not None and time <= drop_older_than

    def is_newest_item(time):
        return newest_time is None or time > newest_time

    def emit_all(emit_older_than):
        while len(sorted_buffer) > 0 and sorted_buffer[0][0] <= emit_older_than:
            time, item = heapq.heappop(sorted_buffer)
            yield item

    for item in wrap_iter:
        time = time_getter(item)

        if is_too_late(time):
            if on_drop:
                on_drop(item)
        else:
            heapq.heappush(sorted_buffer, (time, item))

            if is_newest_item(time):
                newest_time = time
                drop_older_than = time - window_length

                yield from emit_all(drop_older_than)

    yield from emit_all(newest_time)