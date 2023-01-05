import datetime
import json
import types

from .bytewax import Dataflow


class DataflowEncoder(json.JSONEncoder):
    """Custom JSON encoder for a Dataflow

    This class is used in conjunction with the Rust `webserver` module to
    produce a JSON representation of a bytewax Dataflow.

    __getstate__() is a method defined on all of the Python classes we
    create in Rust to return a PyDict representation of that class.
    """

    def default(self, obj):
        # Python 3.11 added __getstate__ to the 'object' type,
        # but for some objects the result is `None`.
        # In those cases, it's probably better to try other options,
        # so we check for the presence of the method, but also that
        # the result is not `None`.
        if hasattr(obj, "__getstate__") and obj.__getstate__() is not None:
            return obj.__getstate__()
        if isinstance(obj, types.BuiltinFunctionType):
            return obj.__name__
        if isinstance(obj, types.MethodDescriptorType):
            return obj.__name__
        if isinstance(obj, types.FunctionType):
            return obj.__name__
        if isinstance(obj, types.BuiltinMethodType):
            return obj.__name__
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        if isinstance(obj, datetime.timedelta):
            return str(obj)

        # Call the default encoder method for any other instance types.
        try:
            return json.JSONEncoder.default(self, obj)
        except TypeError as err:
            raise TypeError(f"{obj} can not be JSON encoded: {err}")


def encode_dataflow(dataflow: Dataflow):
    """Convenience method for calling `json.dumps` with our custom
    DataflowEncoder class from Rust.
    """
    return json.dumps(dataflow, cls=DataflowEncoder, sort_keys=True)
