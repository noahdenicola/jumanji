import enum
from typing import TYPE_CHECKING, Any, Optional, Sequence, TypeVar, Union

if TYPE_CHECKING:  # https://github.com/python/mypy/issues/6239
    from dataclasses import dataclass
else:
    from chex import dataclass

import jax.numpy as jnp
from chex import Array

Action = Array
State = TypeVar("State")
Extra = Optional[Any]


class StepType(enum.IntEnum):
    """Adapted from dm_env.TimeStep with the goal of making the step types jax scalars, to
    avoid weak_type=True. Defines the status of a `TimeStep` within a sequence."""

    # Denotes the first `TimeStep` in a sequence.
    FIRST = jnp.int32(0)
    # Denotes any `TimeStep` in a sequence that is not FIRST or LAST.
    MID = jnp.int32(1)
    # Denotes the last `TimeStep` in a sequence.
    LAST = jnp.int32(2)

    def first(self) -> bool:
        return self is StepType.FIRST

    def mid(self) -> bool:
        return self is StepType.MID

    def last(self) -> bool:
        return self is StepType.LAST


@dataclass
class TimeStep:
    """Copied from dm_env.TimeStep with the goal of making it a Jax Type.
    The original dm_env.TimeStep is not a Jax type because inheriting a namedtuple is
    not treated as a valid Jax type (https://github.com/google/jax/issues/806).

    A `TimeStep` contains the data emitted by an environment at each step of
    interaction. A `TimeStep` holds a `step_type`, an `observation` (typically a
    NumPy array or a dict or list of arrays), and an associated `reward` and
    `discount`.

    The first `TimeStep` in a sequence will have `StepType.FIRST`. The final
    `TimeStep` will have `StepType.LAST`. All other `TimeStep`s in a sequence will
    have `StepType.MID.

    Attributes:
        step_type: A `StepType` enum value.
        reward:  A scalar, NumPy array, nested dict, list or tuple of rewards; or
            `None` if `step_type` is `StepType.FIRST`, i.e. at the start of a
            sequence.
        discount: A scalar, NumPy array, nested dict, list or tuple of discount
            values in the range `[0, 1]`, or `None` if `step_type` is
            `StepType.FIRST`, i.e. at the start of a sequence.
        observation: A NumPy array, or a nested dict, list or tuple of arrays.
            Scalar values that can be cast to NumPy arrays (e.g. Python floats) are
            also valid in place of a scalar array.
    """

    step_type: StepType
    reward: Array
    discount: Array
    observation: Array

    def first(self) -> Array:
        return self.step_type == StepType.FIRST

    def mid(self) -> Array:
        return self.step_type == StepType.MID

    def last(self) -> Array:
        return self.step_type == StepType.LAST


def restart(observation: Array, shape: Union[int, Sequence[int]] = ()) -> TimeStep:
    """Returns a `TimeStep` with `step_type` set to `StepType.FIRST`.

    Args:
        observation: array.
        shape : optional parameter to specify the shape of the rewards and discounts.
            Allows multi-agent environment compatibility. Defaults to () for
            scalar reward and discount.

    Returns:
        TimeStep identified as a reset.
    """
    return TimeStep(
        step_type=StepType.FIRST,
        reward=jnp.zeros(shape, dtype=float),
        discount=jnp.ones(shape, dtype=float),
        observation=observation,
    )


def transition(
    reward: Array,
    observation: Array,
    discount: Optional[Array] = None,
    shape: Union[int, Sequence[int]] = (),
) -> TimeStep:
    """Returns a `TimeStep` with `step_type` set to `StepType.MID`.

    Args:
        reward: array.
        observation: array.
        discount: array.
        shape : optional parameter to specify the shape of the rewards and discounts.
            Allows multi-agent environment compatibility. Defaults to () for
            scalar reward and discount.

    Returns:
        TimeStep identified as a transition.
    """
    discount = discount if discount is not None else jnp.ones(shape, dtype=float)
    return TimeStep(
        step_type=StepType.MID,
        reward=reward,
        discount=discount,
        observation=observation,
    )


def termination(
    reward: Array, observation: Array, shape: Union[int, Sequence[int]] = ()
) -> TimeStep:
    """Returns a `TimeStep` with `step_type` set to `StepType.LAST`.

    Args:
        reward: array.
        observation: array.
        shape : optional parameter to specify the shape of the rewards and discounts.
            Allows multi-agent environment compatibility. Defaults to () for
            scalar reward and discount.

    Returns:
        TimeStep identified as the termination of an episode.
    """
    return TimeStep(
        step_type=StepType.LAST,
        reward=reward,
        discount=jnp.zeros(shape, dtype=float),
        observation=observation,
    )


def truncation(
    reward: Array,
    observation: Array,
    discount: Optional[Array] = None,
    shape: Union[int, Sequence[int]] = (),
) -> TimeStep:
    """Returns a `TimeStep` with `step_type` set to `StepType.LAST`.

    Args:
        reward: array.
        observation: array.
        discount: array.
        shape : optional parameter to specify the shape of the rewards and discounts.
            Allows multi-agent environment compatibility. Defaults to () for
            scalar reward and discount.
    Returns:
        TimeStep identified as the truncation of an episode.
    """
    discount = discount if discount is not None else jnp.ones(shape, dtype=float)
    return TimeStep(
        step_type=StepType.LAST,
        reward=reward,
        discount=discount,
        observation=observation,
    )
