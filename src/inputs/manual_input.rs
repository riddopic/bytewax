use std::task::Poll;

use crate::add_pymethods;
use crate::execution::{WorkerCount, WorkerIndex};
use crate::pyo3_extensions::{TdPyAny, TdPyCallable, TdPyCoroIterator};
use crate::recovery::model::StateBytes;
use crate::{common::StringResult, py_unwrap, try_unwrap};
use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;

use super::{InputBuilder, InputConfig, InputReader};

/// Use a user-defined function that returns an iterable as the input
/// source.
///
/// Because Bytewax's execution is cooperative, the resulting
/// iterators _must not block_ waiting for new data, otherwise pending
/// execution of other steps in the dataflow will be delayed an
/// throughput will be reduced. If you are using a generator and no
/// data is ready yet, have it `yield None` or just `yield` to signal
/// this.
///
/// Args:
///
///   input_builder: `input_builder(worker_index: int, worker_count:
///       int, resume_state: Option[Any]) => Iterator[Tuple[Any,
///       Any]]` Builder function which returns an iterator of
///       2-tuples of `(state, item)`. `item` is the input that
///       worker should introduce into the dataflow. `state` is a
///       snapshot of any internal state it will take to resume this
///       input from its current position _after the current
///       item_. Note that e.g. returning the same list from each
///       worker will result in duplicate data in the dataflow.
///
/// Returns:
///
///   Config object. Pass this as the `input_config` argument of the
///   `bytewax.dataflow.Dataflow.input` operator.
#[pyclass(module = "bytewax.inputs", extends = InputConfig)]
#[pyo3(text_signature = "(input_builder)", subclass)]
#[derive(Clone)]
pub(crate) struct ManualInputConfig {
    #[pyo3(get)]
    pub(crate) input_builder: TdPyCallable,
}

impl InputBuilder for ManualInputConfig {
    fn build(
        &self,
        py: Python,
        worker_index: WorkerIndex,
        worker_count: WorkerCount,
        resume_snapshot: Option<StateBytes>,
    ) -> StringResult<Box<dyn InputReader<TdPyAny>>> {
        Ok(Box::new(ManualInput::new(
            py,
            self.input_builder.clone(),
            worker_index,
            worker_count,
            resume_snapshot,
        )))
    }
}

add_pymethods!(
    ManualInputConfig,
    parent: InputConfig,
    py_args: (input_builder),
    args {
        input_builder: TdPyCallable => Python::with_gil(TdPyCallable::pickle_new)
    }
);

/// Construct a Python iterator for each worker from a builder
/// function.
pub(crate) struct ManualInput {
    pyiter: TdPyCoroIterator,
    last_state: TdPyAny,
}

impl ManualInput {
    pub(crate) fn new(
        py: Python,
        input_builder: TdPyCallable,
        worker_index: WorkerIndex,
        worker_count: WorkerCount,
        resume_snapshot: Option<StateBytes>,
    ) -> Self {
        let resume_state: TdPyAny = resume_snapshot
            .map(StateBytes::de::<TdPyAny>)
            .unwrap_or_else(|| py.None().into());

        let pyiter: TdPyCoroIterator = try_unwrap!(input_builder
            .call1(
                py,
                (worker_index.0, worker_count.0, resume_state.clone_ref(py))
            )?
            .extract(py));

        Self {
            pyiter,
            last_state: resume_state,
        }
    }
}

impl InputReader<TdPyAny> for ManualInput {
    #[tracing::instrument(name = "manual_input", level = "trace", skip_all)]
    fn next(&mut self) -> Poll<Option<TdPyAny>> {
        self.pyiter.next().map(|poll| {
            poll.map(|state_item_pytuple| {
                Python::with_gil(|py| {
                    let (updated_state, item): (TdPyAny, TdPyAny) =
                        tracing::trace_span!("Python input").in_scope(|| {
                            py_unwrap!(
                                state_item_pytuple.extract(py),
                                format!(
                                    "Manual input builders must yield `(state, item)` \
                                two-tuples; got `{state_item_pytuple:?}` instead"
                                )
                            )
                        });
                    self.last_state = updated_state;

                    item
                })
            })
        })
    }

    #[tracing::instrument(name = "manual_input_snapshot", level = "trace", skip_all)]
    fn snapshot(&self) -> StateBytes {
        StateBytes::ser::<TdPyAny>(&self.last_state)
    }
}
