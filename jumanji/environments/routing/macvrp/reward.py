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

from jumanji.environments.routing.macvrp.types import State


class RewardFn(abc.ABC):
    def __init__(self, num_vechicles: int, num_customers: int, map_max: int) -> None:
        self.num_vehicles = num_vechicles
        self.num_customers = num_customers
        self.map_max = map_max
        # This is the maximum negative reward that can be given to an agent.
        self.large_negate_reward = (
            -2
            * self.map_max
            * jax.numpy.sqrt(2)
            * self.num_customers
            * self.num_vehicles
        )

    @abc.abstractmethod
    def __call__(
        self,
        state: State,
        is_done: bool,
    ) -> chex.Numeric:
        """Compute the reward based on the current state, the chosen action, the next state and
        whether the action is valid.
        """


class SparseReward(RewardFn):
    """The negative distance between the current city and the chosen next city to go to length at
    the end of the episode. It also includes the distance to the depot to complete the tour.
    Note that the reward is `-2 * num_nodes * sqrt(2)` if the chosen action is invalid.
    """

    def __call__(
        self,
        state: State,
        is_done: bool,
    ) -> chex.Numeric:
        def compute_episode_reward(state: State) -> float:
            return jax.lax.cond(  # type: ignore
                jax.numpy.any(state.step_count > self.num_customers * 2),
                # Penalise for running into step limit. This is not including max time
                # penalties as the distance penalties are already enough.
                lambda state: self.large_negate_reward,
                lambda state: -state.vehicles.distances.sum()
                - state.vehicles.time_penalties.sum(),
                state,
            )

        # By default, returns the negative distance between the previous and new node.
        reward = jax.lax.select(
            is_done,
            compute_episode_reward(state),
            jax.numpy.float32(0),
        )

        return reward
