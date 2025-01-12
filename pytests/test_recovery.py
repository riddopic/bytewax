from threading import Event

from pytest import fixture, raises

from bytewax.dataflow import Dataflow
from bytewax.execution import run_main, TestingEpochConfig
from bytewax.inputs import TestingInputConfig
from bytewax.outputs import TestingEpochOutputConfig, TestingOutputConfig
from bytewax.recovery import SqliteRecoveryConfig

epoch_config = TestingEpochConfig()


@fixture(params=["SqliteRecoveryConfig"])
def recovery_config(tmp_path, request):
    if request.param == "SqliteRecoveryConfig":
        yield SqliteRecoveryConfig(str(tmp_path))
    else:
        raise ValueError("unknown recovery config type: {request.param!r}")


# We only need to test stateful_map since all other stateful operators
# are implemented using it.


def build_keep_max_dataflow(armed, inp):
    """Builds a dataflow that keeps track of the largest value seen for
    each key, but also allows you to reset the max with a value of
    `None`. Input is `(key, value, should_explode)`. Will throw
    exception if `should_explode` is truthy and `armed` is set.

    """
    flow = Dataflow()

    flow.input("inp", TestingInputConfig(inp))

    def trigger(item):
        """Odd numbers cause exception if armed."""
        key, value, should_explode = item
        if armed.is_set() and should_explode:
            raise RuntimeError("BOOM")
        return key, value

    flow.map(trigger)

    def keep_max(previous_max, new_item):
        if previous_max is None:
            new_max = new_item
        else:
            if new_item is not None:
                new_max = max(previous_max, new_item)
            else:
                new_max = None
        return new_max, new_max

    flow.stateful_map("keep_max", lambda: None, keep_max)

    return flow


def test_recover_with_latest_state(recovery_config):
    armed = Event()
    # Will explode on first run.
    armed.set()

    # Epoch is incremented after each item.
    inp = [
        # Epoch 0
        ("a", 4, False),
        # Epoch 1
        ("b", 4, False),
        # Epoch 2
        # Will fail here on first pass.
        ("a", 1, "BOOM"),
        # Epoch 3
        ("b", 1, False),
    ]
    out = []
    flow = build_keep_max_dataflow(armed, inp)
    flow.capture(TestingEpochOutputConfig(out))

    # First pass.
    with raises(RuntimeError):
        run_main(flow, epoch_config=epoch_config, recovery_config=recovery_config)

    assert sorted(out) == sorted(
        [
            (0, ("a", 4)),
            (1, ("b", 4)),
        ]
    )

    # Disable bomb.
    armed.clear()
    out.clear()

    # Recover.
    run_main(flow, epoch_config=epoch_config, recovery_config=recovery_config)

    # Restarts from failed epoch.
    assert sorted(out) == sorted(
        [
            (2, ("a", 4)),
            (3, ("b", 4)),
        ]
    )


def test_recover_doesnt_gc_last_write(recovery_config):
    armed = Event()
    # Will explode on first run.
    armed.set()

    # Epoch is incremented after each item.
    inp = [
        # Epoch 0
        # "a" is old enough to be GCd by time failure happens, but
        # shouldn't be because the key hasn't been seen again.
        ("a", 4, False),
        # Epoch 1
        ("b", 4, False),
        # Epoch 2
        ("b", 4, False),
        # Epoch 3
        ("b", 4, False),
        # Epoch 4
        ("b", 4, False),
        # Epoch 5
        # Will fail here on first pass.
        ("b", 5, "BOOM"),
        # Epoch 6
        ("a", 1, False),
    ]
    out = []
    flow = build_keep_max_dataflow(armed, inp)
    flow.capture(TestingEpochOutputConfig(out))

    # First pass.
    with raises(RuntimeError):
        run_main(flow, epoch_config=epoch_config, recovery_config=recovery_config)

    assert sorted(out) == sorted(
        [
            (0, ("a", 4)),
            (1, ("b", 4)),
            (2, ("b", 4)),
            (3, ("b", 4)),
            (4, ("b", 4)),
        ]
    )

    # Disable bomb.
    armed.clear()
    out.clear()

    # Recover.
    run_main(flow, epoch_config=epoch_config, recovery_config=recovery_config)

    # Restarts from failed epoch.
    assert sorted(out) == sorted(
        [
            (5, ("b", 5)),
            # Remembered "a": 4
            (6, ("a", 4)),
        ]
    )


def test_recover_respects_delete(recovery_config):
    armed = Event()
    # Will explode on first run.
    armed.set()

    # Epoch is incremented after each item.
    inp = [
        # Epoch 0
        ("a", 4, False),
        # Epoch 1
        ("b", 4, False),
        # Epoch 2
        # Delete state for key.
        ("a", None, False),
        # Epoch 3
        ("b", 2, False),
        # Epoch 4
        # Will fail here on first pass.
        ("b", 5, "BOOM"),
        # Epoch 5
        # Should be max for "a" on resume.
        ("a", 2, False),
    ]
    out = []
    flow = build_keep_max_dataflow(armed, inp)
    flow.capture(TestingEpochOutputConfig(out))

    # First pass.
    with raises(RuntimeError):
        run_main(flow, epoch_config=epoch_config, recovery_config=recovery_config)

    assert sorted(out) == sorted(
        [
            (0, ("a", 4)),
            (1, ("b", 4)),
            (2, ("a", None)),
            (3, ("b", 4)),
        ]
    )

    # Disable bomb.
    armed.clear()
    out.clear()

    # Recover.
    run_main(flow, epoch_config=epoch_config, recovery_config=recovery_config)

    # Restarts from failed epoch.
    assert sorted(out) == sorted(
        [
            (4, ("b", 5)),
            # Notice not 4.
            (5, ("a", 2)),
        ]
    )


def test_continuation(entry_point, inp, out, recovery_config):
    # Not used for continuation, but we want to re-use
    # build_keep_max_dataflow.
    armed = Event()

    # Since we're modifying the input, use the fixture so it works
    # across processes. Currently, `inp = []`.
    inp.extend(
        [
            ("a", 4, False),
            ("b", 4, False),
        ]
    )
    flow = build_keep_max_dataflow(armed, inp)
    flow.capture(TestingOutputConfig(out))

    entry_point(flow, recovery_config=recovery_config)

    assert sorted(out) == sorted(
        [
            ("a", 4),
            ("b", 4),
        ]
    )

    # Add new input. Don't clear because `TestingInputConfig` needs
    # the initial items so the resume epoch skips to here.
    inp.extend(
        [
            ("a", 1, False),
            ("b", 5, False),
        ]
    )
    # Unfortunately `ListProxy`, which we'd use in the cluster entry
    # point, does not have `clear`.
    del out[:]

    # Continue.
    entry_point(flow, recovery_config=recovery_config)

    # Incorporates new input.
    assert sorted(out) == sorted(
        [
            ("a", 4),
            ("b", 5),
        ]
    )
