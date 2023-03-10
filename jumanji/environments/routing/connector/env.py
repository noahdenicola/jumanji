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

from typing import Dict, Optional, Sequence, Tuple

import chex
import jax
import jax.numpy as jnp
import matplotlib
from numpy.typing import NDArray

from jumanji import specs
from jumanji.env import Environment
from jumanji.environments.routing.connector.constants import (
    AGENT_INITIAL_VALUE,
    NOOP,
    PATH,
)
from jumanji.environments.routing.connector.env_viewer import ConnectorViewer
from jumanji.environments.routing.connector.generator import (
    Generator,
    UniformRandomGenerator,
)
from jumanji.environments.routing.connector.reward import DenseRewardFn, RewardFn
from jumanji.environments.routing.connector.types import Agent, Observation, State
from jumanji.environments.routing.connector.utils import (
    connected_or_blocked,
    get_agent_grid,
    get_correction_mask,
    is_valid_position,
    move_agent,
    move_position,
    switch_perspective,
)
from jumanji.types import TimeStep, restart, termination, transition


class Connector(Environment[State]):
    """The `Connector` environment is a multi-agent gridworld problem where each agent must connect a
    start to a target. However, when moving through this gridworld the agent leaves an impassable
    trail behind it. Therefore, agents must connect to their targets without overlapping the routes
    taken by any other agent.

    - observation - `Observation`
        - action mask: jax array (bool) of shape (num_agents, 5).
        - step_count: jax array (int32) of shape ()
            the current episode step.
        - grid: jax array (int32) of shape (num_agents, size, size)
            - each 2d array (size, size) along axis 0 is the agent's local observation.
            - agents have ids from 0 to (num_agents - 1)
            - with 2 agents you might have a grid like this:
              4 0 1
              5 0 1
              6 3 2
              which means agent 1 has moved from the top right of the grid down and is currently in
              the bottom right corner and is aiming to get to the middle bottom cell. Agent 2
              started in the top left and moved down once towards its target in the bottom left.

              This would just be agent 0's view, the numbers would be flipped for agent 1's view.
              So the full observation would be of shape (2, 3, 3).

    - action: jax array (int32) of shape (num_agents,):
        - can take the values [0,1,2,3,4] which correspond to [No Op, Up, Right, Down, Left].
        - each value in the array corresponds to an agent's action.

    - reward: jax array (float) of shape ():
        - dense: each agent is given 1.0 if it connects on that step, otherwise 0.0. Additionally,
            each agent that has not connected receives a penalty reward of -0.03.

    - episode termination: if an agent can't move, or the time limit is reached, or the agent
        connects to its target, it is considered done. Once all agents are done, the episode
        terminates. The timestep discounts are of shape (num_agents,).

    - state: State:
        - key: jax PRNG key used to randomly spawn agents and targets.
        - grid: jax array (int32) of shape (size, size) which corresponds to agent 0's observation.
        - step_count: jax array (int32) of shape () number of steps elapsed in the current episode.

    ```python
    from jumanji.environments import Connector
    env = Connector()
    key = jax.random.key(0)
    state, timestep = jax.jit(env.reset)(key)
    env.render(state)
    action = env.action_spec().generate_value()
    state, timestep = jax.jit(env.step)(state, action)
    env.render(state)
    ```
    """

    def __init__(
        self,
        generator: Optional[Generator] = None,
        reward_fn: Optional[RewardFn] = None,
        time_limit: int = 50,
        render_mode: str = "human",
    ) -> None:
        """Create the `Connector` environment.

        Args:
            generator: `Generator` whose `__call__` instantiates an environment instance.
                Implemented options are [`UniformRandomGenerator`].
                Defaults to `UniformRandomGenerator`.
            reward_fn: class of type `RewardFn`, whose `__call__` is used as a reward function.
                Implemented options are [`DenseRewardFn`]. Defaults to `DenseRewardFn`.
            time_limit: the number of steps allowed before an episode terminates. Defaults to 50.
        """
        self._generator = generator or UniformRandomGenerator()
        self._reward_fn = reward_fn or DenseRewardFn()
        self.time_limit = time_limit
        self.num_agents = self._generator.num_agents
        self.grid_size = self._generator.grid_size
        self._agent_ids = jnp.arange(self.num_agents)
        self._renderer = ConnectorViewer("Connector", self.num_agents, render_mode)

    def reset(self, key: chex.PRNGKey) -> Tuple[State, TimeStep[Observation]]:
        """Resets the environment.

        Args:
            key: used to randomly generate the connector grid.

        Returns:
            state: `State` object corresponding to the new state of the environment.
            timestep: `TimeStep` object corresponding to the initial environment timestep.
        """
        state = self._generator(key)

        action_mask = jax.vmap(self._get_action_mask, (0, None))(
            state.agents, state.grid
        )
        observation = Observation(
            grid=self._obs_from_grid(state.grid),
            action_mask=action_mask,
            step_count=state.step_count,
        )
        extras = self._get_extras(state)
        timestep = restart(
            observation=observation, extras=extras, shape=(self.num_agents,)
        )
        return state, timestep

    def step(
        self, state: State, action: chex.Array
    ) -> Tuple[State, TimeStep[Observation]]:
        """Perform an environment step.

        Args:
            state: State object containing the dynamics of the environment.
            action: Array containing the actions to take for each agent.
                - 0 no op
                - 1 move up
                - 2 move right
                - 3 move down
                - 4 move left

        Returns:
            state: `State` object corresponding to the next state of the environment.
            timestep: `TimeStep` object corresponding the timestep returned by the environment.
        """
        agents, grid = self._step_agents(state, action)
        new_state = State(
            grid=grid, step_count=state.step_count + 1, agents=agents, key=state.key
        )

        # Construct timestep: get observations, rewards, discounts
        grids = self._obs_from_grid(grid)
        reward = self._reward_fn(state, action, new_state)
        action_mask = jax.vmap(self._get_action_mask, (0, None))(agents, grid)
        observation = Observation(
            grid=grids, action_mask=action_mask, step_count=new_state.step_count
        )

        dones = jax.vmap(connected_or_blocked)(agents, action_mask)
        discount = jnp.asarray(jnp.logical_not(dones), dtype=float)
        extras = self._get_extras(new_state)
        timestep = jax.lax.cond(
            dones.all() | (new_state.step_count >= self.time_limit),
            lambda: termination(
                reward=reward,
                observation=observation,
                extras=extras,
                shape=self.num_agents,
            ),
            lambda: transition(
                reward=reward,
                observation=observation,
                discount=discount,
                extras=extras,
                shape=self.num_agents,
            ),
        )

        return new_state, timestep

    def _step_agents(
        self, state: State, action: chex.Array
    ) -> Tuple[Agent, chex.Array]:
        """Steps all agents at the same time correcting for possible collisions.

        If a collision occurs we place the agent with the lower `agent_id` in its previous position.

        Returns:
            Tuple: (agents, grid) after having applied each agents' action
        """
        agent_ids = jnp.arange(self.num_agents)
        # Step all agents at the same time (separately) and return all of the grids
        agents, grids = jax.vmap(self._step_agent, in_axes=(0, None, 0))(
            state.agents, state.grid, action
        )

        # Get grids with only values related to a single agent.
        # For example: remove all other agents from agent 1's grid. Do this for all agents.
        agent_grids = jax.vmap(get_agent_grid)(agent_ids, grids)
        joined_grid = jnp.max(agent_grids, 0)  # join the grids

        # Create a correction mask for possible collisions (see the docs of `get_correction_mask`)
        correction_fn = jax.vmap(get_correction_mask, in_axes=(None, None, 0))
        correction_masks, collided_agents = correction_fn(
            state.grid, joined_grid, agent_ids
        )
        correction_mask = jnp.sum(correction_masks, 0)

        # Correct state.agents
        # Get the correct agents, either old agents (if collision) or new agents if no collision
        agents = jax.vmap(
            lambda collided, old_agent, new_agent: jax.lax.cond(
                collided,
                lambda: old_agent,
                lambda: new_agent,
            )
        )(collided_agents, state.agents, agents)
        # Create the new grid by fixing old one with correction mask and adding the obstacles
        return agents, joined_grid + correction_mask

    def _step_agent(
        self, agent: Agent, grid: chex.Array, action: chex.Numeric
    ) -> Tuple[Agent, chex.Array]:
        """Moves the agent according to the given action if it is possible.

        Returns:
            Tuple: (agent, grid) after having applied the given action.
        """
        new_pos = move_position(agent.position, action)

        new_agent, new_grid = jax.lax.cond(
            is_valid_position(grid, agent, new_pos) & (action != NOOP),
            move_agent,
            lambda *_: (agent, grid),
            agent,
            grid,
            new_pos,
        )

        return new_agent, new_grid

    def _obs_from_grid(self, grid: chex.Array) -> chex.Array:
        """Gets the observation vector for all agents."""
        return jax.vmap(switch_perspective, (None, 0, None))(
            grid, self._agent_ids, self.num_agents
        )

    def _get_action_mask(self, agent: Agent, grid: chex.Array) -> chex.Array:
        """Gets an agent's action mask."""
        # Don't check action 0 because no-op is always valid
        actions = jnp.arange(1, 5)

        def is_valid_action(action: int) -> chex.Array:
            agent_pos = move_position(agent.position, action)
            return is_valid_position(grid, agent, agent_pos)

        mask = jnp.ones(5, dtype=bool)
        mask = mask.at[actions].set(jax.vmap(is_valid_action)(actions))
        return mask

    def _get_extras(self, state: State) -> Dict:
        """Computes extras metrics to be return within the timestep."""
        offset = AGENT_INITIAL_VALUE
        total_path_length = jnp.sum((offset + (state.grid - offset) % 3) == PATH)
        # Add agents' head
        total_path_length += self.num_agents
        extras = {
            "num_connections": jnp.sum(state.agents.connected),
            "ratio_connections": jnp.mean(state.agents.connected),
            "total_path_length": total_path_length,
        }
        return extras

    def render(self, state: State) -> Optional[NDArray]:
        """Render the given state of the environment.

        Args:
            state: `State` object containing the current environment state.
        """
        return self._renderer.render(state.grid)

    def animate(
        self,
        states: Sequence[State],
        interval: int = 200,
        save: bool = False,
        path: str = "./connector.gif",
    ) -> matplotlib.animation.FuncAnimation:
        """Create an animation from a sequence of states.

        Args:
            states: sequence of `State` corresponding to subsequent timesteps.
            interval: delay between frames in milliseconds, default to 200.
            save: whether to save the animation (as a gif).
            path: where to save the animation (as a gif).

        Returns:
            animation that can export to gif, mp4, or render with HTML.
        """

        grids = [state.grid for state in states]
        return self._renderer.animate(grids, interval, save, path)

    def observation_spec(self) -> specs.Spec[Observation]:
        """Specifications of the observation of the `Connector` environment.

        Returns:
            Spec for the `Observation` whose fields are:
            - grid: BoundedArray (int32) of shape (num_agents, grid_size, grid_size).
            - action_mask: BoundedArray (bool) of shape (num_agents, 5).
            - step_count: BoundedArray (int32) of shape ().
        """
        grid = specs.BoundedArray(
            shape=(self.num_agents, self.grid_size, self.grid_size),
            dtype=jnp.int32,
            name="grid",
            minimum=0,
            maximum=self.num_agents * 3 + AGENT_INITIAL_VALUE,
        )
        action_mask = specs.BoundedArray(
            shape=(self.num_agents, 5),
            dtype=bool,
            minimum=False,
            maximum=True,
            name="action_mask",
        )
        step_count = specs.BoundedArray(
            shape=(),
            dtype=jnp.int32,
            minimum=0,
            maximum=self.time_limit,
            name="step_count",
        )
        return specs.Spec(
            Observation,
            "ObservationSpec",
            grid=grid,
            action_mask=action_mask,
            step_count=step_count,
        )

    def action_spec(self) -> specs.MultiDiscreteArray:
        """Returns the action spec for the Connector environment.

        5 actions: [0,1,2,3,4] -> [No Op, Up, Right, Down, Left]. Since this is a multi-agent
        environment, the environment expects an array of actions of shape (num_agents,).

        Returns:
            observation_spec: `MultiDiscreteArray` of shape (num_agents,).
        """
        return specs.MultiDiscreteArray(
            num_values=jnp.array([5] * self.num_agents),
            dtype=jnp.int32,
            name="action",
        )

    def reward_spec(self) -> specs.Array:
        """
        Returns:
            reward_spec: a `specs.Array` spec of shape (num_agents,). One for each agent.
        """
        return specs.Array(shape=(self.num_agents,), dtype=float, name="reward")

    def discount_spec(self) -> specs.BoundedArray:
        """
        Returns:
            discount_spec: a `specs.Array` spec of shape (num_agents,). One for each agent
        """
        return specs.BoundedArray(
            shape=(self.num_agents,),
            dtype=float,
            minimum=0.0,
            maximum=1.0,
            name="discount",
        )
