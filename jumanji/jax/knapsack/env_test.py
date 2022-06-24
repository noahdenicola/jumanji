import chex
import jax
import pytest
from jax import numpy as jnp
from jax import random

from jumanji.jax.knapsack.env import Knapsack
from jumanji.jax.knapsack.types import State
from jumanji.jax.types import TimeStep
from jumanji.testing.fakes import FakeAgent
from jumanji.testing.pytrees import assert_is_jax_array_tree
from validation import JaxEnvironmentLoop


@pytest.fixture
def knapsack_env() -> Knapsack:
    """Instantiates a default knapsack environment."""
    return Knapsack()


def test_knapsack__reset(knapsack_env: Knapsack) -> None:
    """Validates the jitted reset of the environment."""

    reset_fn = jax.jit(knapsack_env.reset)
    key = random.PRNGKey(0)
    state, timestep, _ = reset_fn(key)
    assert isinstance(timestep, TimeStep)
    assert isinstance(state, State)
    assert state.remaining_budget <= knapsack_env.total_budget
    assert state.remaining_budget >= 0
    assert (
        state.remaining_budget
        == knapsack_env.total_budget - state.problem[state.last_item, 0]
    )

    assert state.used_mask.sum() == 1
    # Check that the state is made of DeviceArrays, this is false for the non-jitted
    # reset function since unpacking random.split returns numpy arrays and not device arrays.
    assert_is_jax_array_tree(state)


def test_knapsack__step(knapsack_env: Knapsack) -> None:
    """Validates the jitted step of the environment."""
    chex.clear_trace_counter()

    step_fn = chex.assert_max_traces(knapsack_env.step, n=1)
    step_fn = jax.jit(step_fn)

    key = random.PRNGKey(0)
    state, timestep, _ = knapsack_env.reset(key)

    action_already_taken = state.last_item
    new_action = action_already_taken - 1 if action_already_taken > 0 else 0

    new_state, next_timestep, _ = step_fn(state, new_action)

    # Check that the state has changed
    assert not jnp.array_equal(new_state.used_mask, state.used_mask)
    assert not jnp.array_equal(new_state.last_item, state.last_item)
    assert not jnp.array_equal(new_state.remaining_budget, state.remaining_budget)

    # Check that the state is made of DeviceArrays, this is false for the non-jitted
    # step function since unpacking random.split returns numpy arrays and not device arrays.
    assert_is_jax_array_tree(new_state)

    # Check token was inserted as expected
    assert new_state.used_mask[new_action] == 1

    # New step with same action should be invalid
    state = new_state

    new_state, next_timestep, _ = step_fn(state, new_action)

    # Check that the state has not changed
    assert jnp.array_equal(new_state.used_mask, state.used_mask)
    assert jnp.array_equal(new_state.last_item, state.last_item)
    assert jnp.array_equal(new_state.remaining_budget, state.remaining_budget)


def test_knapsack__does_not_smoke(
    knapsack_env: Knapsack, capsys: pytest.CaptureFixture
) -> None:
    """Tests that we can run the jitted JaxEnvironmentLoop without any errors."""
    fake_agent = FakeAgent()
    jax_environment_loop = JaxEnvironmentLoop(
        knapsack_env, fake_agent, n_steps=1, batch_size=2
    )
    jax_environment_loop.run(num_steps=3)
    assert capsys.readouterr().out


def test_knapsackenv__trajectory_action(knapsack_env: Knapsack) -> None:
    """Checks that the agent stops when the remaining budget does not allow extra items
    and that the appropriate reward is received
    """

    key = random.PRNGKey(0)
    state, timestep, _ = knapsack_env.reset(key)

    while not timestep.last():
        # check that budget remains positive
        assert state.remaining_budget > 0

        # check that the reward is 0 while trajectory is not done
        assert timestep.reward == 0

        state, timestep, _ = knapsack_env.step(
            state, (state.last_item + 1) % knapsack_env.problem_size
        )

    # check that the reward is positive when trajectory is done
    assert timestep.reward > 0

    # check that no action can be taken: remaining items are to large
    assert (
        knapsack_env._state_to_observation(state).invalid_mask.sum()
        == knapsack_env.problem_size
    )
    assert timestep.last()


def test_knapsackenv__invalid_action(knapsack_env: Knapsack) -> None:
    """Checks that an invalid action leads to a termination
    and the appropriate reward is received
    """

    key = random.PRNGKey(0)
    state, timestep, _ = knapsack_env.reset(key)

    first_item = state.first_item
    actions = (
        jnp.array([first_item + 1, first_item + 2, first_item + 2])
        % knapsack_env.problem_size
    )

    for a in actions:
        assert timestep.reward == 0
        assert timestep.step_type < 2
        state, timestep, _ = knapsack_env.step(state, a)

    # last action is invalid because it was already taken
    assert timestep.reward > 0
    assert timestep.last()