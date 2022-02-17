from bytewax import Dataflow, run_cluster, run_sync

from pytest import mark, raises


def test_run_sync():
    flow = Dataflow()
    flow.map(lambda x: x + 1)
    flow.capture()

    out = run_sync(flow, enumerate(range(3)))
    assert sorted(out) == sorted([(0, 1), (1, 2), (2, 3)])


def test_run_cluster():
    flow = Dataflow()
    flow.map(lambda x: x + 1)
    flow.capture()

    out = run_cluster(flow, enumerate(range(3)), proc_count=2)
    assert sorted(out) == sorted([(0, 1), (1, 2), (2, 3)])


def test_run_sync_reraises_exception():
    def boom(item):
        raise RuntimeError()

    flow = Dataflow()
    flow.map(boom)

    with raises(RuntimeError):
        run_sync(flow, enumerate(range(3)))


@mark.skip(
    reason="Timely is currently double panicking in cluster mode and that causes pool.join() to hang; it can be ctrl-c'd though"
)
def test_run_cluster_reraises_exception():
    def boom(item):
        if item == 0:
            raise RuntimeError()
        else:
            return item

    flow = Dataflow()
    flow.map(boom)

    with raises(RuntimeError):
        run_cluster(flow, enumerate(range(3)), proc_count=2)