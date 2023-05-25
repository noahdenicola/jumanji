# Copyright 2022 InstaDeep Ltd. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import abc

import chex
import jax
import jax.numpy as jnp

from jumanji.environments.packing.jigsaw.types import State


class RewardFn(abc.ABC):
    @abc.abstractmethod
    def __call__(
        self,
        state: State,
        action: chex.Numeric,
        next_state: State,
        is_valid: bool,
        is_done: bool,
    ) -> chex.Numeric:
        """Compute the reward based on the current state, the chosen action,
        whether the action is valid and whether the episode is terminated.
        """


class DenseReward(RewardFn):
    """Reward function for the dense reward setting."""

    def __call__(
        self,
        state: State,
        action: chex.Numeric,
        next_state: State,
        is_valid: bool,
        is_done: bool,
    ) -> chex.Numeric:
        """Compute the reward based on the current state, the chosen action,
        whether the action is valid and whether the episode is terminated.

        Note here, that the action taken is not the raw action received from the
        agent, but the piece the agent opted to place on the board.
        """
        del is_done
        del next_state

        reward = jax.lax.cond(
            is_valid,
            lambda: jnp.sum(jnp.equal(state.solved_board, action), dtype=jnp.float32),
            lambda: jnp.float32(0.0),
        )

        return reward


class SparseReward(RewardFn):
    """Reward function for the dense reward setting."""

    def __call__(
        self,
        state: State,
        action: chex.Numeric,
        next_state: State,
        is_valid: bool,
        is_done: bool,
    ) -> chex.Numeric:
        """Compute the reward based on the current state, the chosen action,
        the next state, whether the action is valid and whether the episode is terminated.

        Note here, that the action taken is not the raw action received from the
        agent, but the piece the agent opted to place on the board.
        """

        del action

        completed_correctly = (
            is_done
            & jnp.all(jnp.equal(state.solved_board, next_state.current_board))
            & is_valid
        )

        reward = jax.lax.cond(
            completed_correctly,
            lambda: jnp.float32(1.0),
            lambda: jnp.float32(0.0),
        )

        return reward