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

from typing import Optional, Sequence, Tuple

import chex
import jax
import jax.numpy as jnp
import matplotlib

from jumanji import specs
from jumanji.env import Environment
from jumanji.environments.routing.cvrp.constants import DEPOT_IDX
from jumanji.environments.routing.cvrp.env_viewer import CVRPViewer
from jumanji.environments.routing.cvrp.reward import DenseReward, RewardFn
from jumanji.environments.routing.cvrp.types import Observation, State
from jumanji.types import TimeStep, restart, termination, transition


class CVRP(Environment[State]):
    """Capacitated Vehicle Routing Problem (CVRP) environment as described in [1].
    - observation: `Observation`
        - coordinates: jax array (float) of shape (num_nodes + 1, 2)
            the coordinates of each node and the depot.
        - demands: jax array (float) of shape (num_nodes + 1,)
            the associated cost of each node and the depot (0.0 for the depot).
        - unvisited_nodes: jax array (bool) of shape (num_nodes + 1,)
            indicates nodes that remain to be visited.
        - position: jax array (int32) of shape ()
            the index of the last visited node.
        - trajectory: jax array (int32) of shape (2 * num_nodes,)
            array of node indices defining the route (set to DEPOT_IDX if not filled yet).
        - capacity: jax array (float) of shape ()
            the current capacity of the vehicle.
        - action_mask: jax array (bool) of shape (num_nodes + 1,)
            binary mask (False/True <--> invalid/valid action).

    - action: jax array (int32) of shape ()
        [0, ..., num_nodes] -> node to visit. 0 corresponds to visiting the depot.

    - reward: jax array (float) of shape (), could be either:
        - dense: the negative distance between the current node and the chosen next node to go to.
            For the last node, it also includes the distance to the depot to complete the tour.
        - sparse: the negative tour length at the end of the episode. The tour length is defined
            as the sum of the distances between consecutive nodes.
        In both cases, the reward is a large negative penalty of `-2 * num_nodes * sqrt(2)` if the
        action is invalid, e.g. a previously selected node other than the depot is selected again.

    - episode termination:
        - if no action can be performed, i.e. all nodes have been visited.
        - if an invalid action is taken, i.e. a previously visited city other than the depot is
            chosen.

    - state: `State`
        - coordinates: jax array (float) of shape (num_nodes + 1, 2)
            the coordinates of each node and the depot.
        - demands: jax array (int32) of shape (num_nodes + 1,)
            the associated cost of each node and the depot (0.0 for the depot).
        - position: jax array (int32)
            the index of the last visited node.
        - capacity: jax array (int32)
            the current capacity of the vehicle.
        - visited_mask: jax array (bool) of shape (num_nodes + 1,)
            binary mask (False/True <--> not visited/visited).
        - trajectory: jax array (int32) of shape (2 * num_nodes,)
            identifiers of the nodes that have been visited (set to DEPOT_IDX if not filled yet).
        - num_visits: int32
            number of actions that have been taken (i.e., unique visits).

    [1] Toth P., Vigo D. (2014). "Vehicle routing: problems, methods, and applications".
    """

    def __init__(
        self,
        num_nodes: int = 20,
        max_capacity: int = 30,
        max_demand: int = 10,
        reward_fn: Optional[RewardFn] = None,
        render_mode: str = "human",
    ):
        """Instantiates a `CVRP` environment.

        Args:
            num_nodes: number of city nodes in the environment.
                Defaults to 20.
            max_capacity: maximum capacity of the vehicle.
                Defaults to 30.
            max_demand: maximum demand of each node.
                Defaults to 10.
            reward_fn: `RewardFn` whose `__call__` method computes the reward of an environment
                transition. The function must compute the reward based on the current state,
                the chosen action, the next state and whether the action is valid.
                Implemented options are [`DenseReward`, `SparseReward`]. Defaults to `DenseReward`.
            render_mode: string that defines the mode of rendering.
                Choices are ["human, "rgb"], defaults to "human".
        """

        if max_capacity < max_demand:
            raise ValueError(
                f"The demand associated with each node must be lower than the maximum capacity, "
                f"hence the maximum capacity must be >= {max_demand}."
            )
        self.num_nodes = num_nodes
        self.max_capacity = max_capacity
        self.max_demand = max_demand
        self.reward_fn = reward_fn or DenseReward()
        self._env_viewer = CVRPViewer(
            name="CVRP",
            num_cities=self.num_nodes,
            render_mode=render_mode,
        )

    def __repr__(self) -> str:
        return (
            f"CVRP(num_nodes={self.num_nodes}, max_capacity={self.max_capacity}, "
            f"max_demand={self.max_demand})"
        )

    def reset(self, key: chex.PRNGKey) -> Tuple[State, TimeStep[Observation]]:
        """Resets the environment.

        Args:
            key: used to randomly generate the coordinates.

        Returns:
             state: `State` object corresponding to the new state of the environment.
             timestep: `TimeStep` object corresponding to the first timestep returned by the
                environment.
        """
        coordinates_key, demands_key = jax.random.split(key)
        coordinates = jax.random.uniform(
            coordinates_key, (self.num_nodes + 1, 2), minval=0, maxval=1
        )
        demands = jax.random.randint(
            demands_key, (self.num_nodes + 1,), minval=1, maxval=self.max_demand
        )
        demands = demands.at[DEPOT_IDX].set(0)
        visited_mask = jnp.zeros(self.num_nodes + 1, dtype=bool).at[DEPOT_IDX].set(True)
        state = State(
            coordinates=coordinates,
            demands=demands,
            position=jnp.array(DEPOT_IDX, jnp.int32),
            capacity=jnp.array(self.max_capacity, jnp.int32),
            visited_mask=visited_mask,
            trajectory=jnp.full(2 * self.num_nodes, DEPOT_IDX, jnp.int32),
            num_total_visits=jnp.array(1, jnp.int32),
        )
        timestep = restart(observation=self._state_to_observation(state))
        return state, timestep

    def step(
        self, state: State, action: chex.Numeric
    ) -> Tuple[State, TimeStep[Observation]]:
        """Run one timestep of the environment's dynamics.

        Args:
            state: State object containing the dynamics of the environment.
            action: Array containing the index of the next node to visit.

        Returns:
            state, timestep: next state of the environment and timestep to be observed.
        """
        node_demand = state.demands[action]
        node_is_visited = state.visited_mask[action]
        is_valid = ~node_is_visited & (state.capacity >= node_demand)

        next_state = jax.lax.cond(
            is_valid,
            self._update_state,
            lambda *_: state,
            state,
            action,
        )

        reward = self.reward_fn(state, action, next_state, is_valid)
        observation = self._state_to_observation(next_state)

        # Terminate if all nodes have been visited or the action is invalid.
        is_done = next_state.visited_mask.all() | ~is_valid

        timestep = jax.lax.cond(
            is_done,
            termination,
            transition,
            reward,
            observation,
        )
        return next_state, timestep

    def observation_spec(self) -> specs.Spec[Observation]:
        """Returns the observation spec.

        Returns:
            Spec for the `Observation` whose fields are:
            - coordinates: BoundedArray (float) of shape (num_nodes + 1, 2).
            - demands: BoundedArray (float) of shape (num_nodes + 1,).
            - unvisited_nodes: BoundedArray (bool) of shape (num_nodes + 1,).
            - position: DiscreteArray (num_values = num_nodes + 1) of shape ().
            - trajectory: BoundedArray (int32) of shape (2 * num_nodes,).
            - capacity: BoundedArray (float) of shape ().
            - action_mask: BoundedArray (bool) of shape (num_nodes + 1,).
        """
        coordinates = specs.BoundedArray(
            shape=(self.num_nodes + 1, 2),
            minimum=0.0,
            maximum=1.0,
            dtype=float,
            name="coordinates",
        )
        demands = specs.BoundedArray(
            shape=(self.num_nodes + 1,),
            minimum=0.0,
            maximum=1.0,
            dtype=float,
            name="demands",
        )
        unvisited_nodes = specs.BoundedArray(
            shape=(self.num_nodes + 1,),
            dtype=bool,
            minimum=False,
            maximum=True,
            name="unvisited_nodes",
        )
        position = specs.DiscreteArray(
            self.num_nodes + 1, dtype=jnp.int32, name="position"
        )
        trajectory = specs.BoundedArray(
            shape=(2 * self.num_nodes,),
            minimum=0,
            maximum=self.num_nodes + 1,
            dtype=jnp.int32,
            name="trajectory",
        )
        capacity = specs.BoundedArray(
            shape=(), minimum=0.0, maximum=1.0, dtype=float, name="capacity"
        )
        action_mask = specs.BoundedArray(
            shape=(self.num_nodes + 1,),
            dtype=bool,
            minimum=False,
            maximum=True,
            name="action_mask",
        )
        return specs.Spec(
            Observation,
            "ObservationSpec",
            coordinates=coordinates,
            demands=demands,
            unvisited_nodes=unvisited_nodes,
            position=position,
            trajectory=trajectory,
            capacity=capacity,
            action_mask=action_mask,
        )

    def action_spec(self) -> specs.DiscreteArray:
        """Returns the action spec.

        Returns:
            action_spec: a `specs.DiscreteArray` spec.
        """
        return specs.DiscreteArray(self.num_nodes + 1, name="action")

    def render(self, state: State) -> Optional[chex.ArrayNumpy]:
        """Render the given state of the environment. This rendering shows the layout of the tour so
         far with the cities as circles, and the depot as a square.

        Args:
            state: environment state to render.

        Returns:
            rgb_array: the RGB image of the state as an array.
        """
        return self._env_viewer.render(state)

    def animate(
        self,
        states: Sequence[State],
        interval: int = 200,
        save: bool = False,
        path: str = "./cvrp.gif",
    ) -> matplotlib.animation.FuncAnimation:
        """Creates an animated gif of the CVRP environment based on the sequence of states.

        Args:
            states: sequence of environment states corresponding to consecutive timesteps.
            interval: delay between frames in milliseconds, default to 200.
            save: whether to save the animation to a file.
            path: the path to save the animation file.

        Returns:
            animation.FuncAnimation: the animation object that was created.
        """
        return self._env_viewer.animate(
            states=states, interval=interval, save=save, path=path
        )

    def _update_state(self, state: State, action: chex.Numeric) -> State:
        """Updates the state of the environment.

        Args:
            state: State object containing the dynamics of the environment.
            action: int32, index of the next node to visit.

        Returns:
            state: State object corresponding to the new state of the environment.
        """
        capacity = jax.lax.select(
            action == DEPOT_IDX,
            self.max_capacity,
            state.capacity - state.demands[action],
        )

        # Set depot to False (valid to visit) since it can be visited multiple times
        visited_mask = state.visited_mask.at[DEPOT_IDX].set(False)

        return State(
            coordinates=state.coordinates,
            demands=state.demands,
            position=action,
            capacity=capacity,
            visited_mask=visited_mask.at[action].set(True),
            trajectory=state.trajectory.at[state.num_total_visits].set(action),
            num_total_visits=state.num_total_visits + 1,
        )

    def _state_to_observation(self, state: State) -> Observation:
        """Converts a state into an observation.

        Args:
            state: `State` object containing the dynamics of the environment.

        Returns:
            observation: `Observation` object containing the observation of the environment.
        """
        # A node is not reachable if it has already been visited or if the vehicle does not have
        # enough capacity to cover its demand.
        action_mask = ~state.visited_mask & (state.capacity >= state.demands)

        # The depot is reachable if we are not at it already.
        action_mask = action_mask.at[DEPOT_IDX].set(state.position != DEPOT_IDX)

        return Observation(
            coordinates=state.coordinates,
            demands=jnp.asarray(state.demands / self.max_capacity, float),
            unvisited_nodes=~state.visited_mask,
            position=state.position,
            trajectory=state.trajectory,
            capacity=jnp.asarray(state.capacity / self.max_capacity, float),
            action_mask=action_mask,
        )