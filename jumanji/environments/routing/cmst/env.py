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

from typing import Any, Optional, Sequence, Tuple, Type

import chex
import jax
import jax.numpy as jnp

from jumanji import specs
from jumanji.env import Environment
from jumanji.environments.routing.cmst.constants import (
    DUMMY_NODE,
    EMPTY_NODE,
    INVALID_ALREADY_TRAVERSED,
    INVALID_CHOICE,
    INVALID_NODE,
    INVALID_TIE_BREAK,
    UTILITY_NODE,
)
from jumanji.environments.routing.cmst.generator import Generator, SplitRandomGenerator
from jumanji.environments.routing.cmst.reward import DefaultRewardFn, RewardFn
from jumanji.environments.routing.cmst.types import Observation, State
from jumanji.environments.routing.cmst.viewer import Renderer
from jumanji.types import TimeStep, restart, termination, transition, truncation


class CoopMinSpanTree(Environment[State]):
    """The cooperative minimum spanning tree (CMST) environment consists of a random connected graph
    with groups of nodes (same node types) that need to be connected.
    The goal of the environment is to connect all nodes of the same type together
    without using the same utility nodes (nodes that do not belong to any group of nodes).

    Note: routing problems are randomly generated and may not be solvable!
    Additionally, the total number of nodes should be at least 20% more than
    the number of nodes we want to connect. This is to guarantee we have enough remaining
    nodes to create a path with all the nodes we want to connect. In the current implementation,
    the total number of nodes to connect (by all agents) should be less than
    80% of the total number of nodes. An exception will be raised if the number of nodes is
    not greater than (0.8 x num_agents x num_nodes_per_agent).

    - observation: Observation
        - node_types: jax array (int) of shape (num_nodes):
            the component type of each node (-1 represents utility nodes).
        - adj_matrix: jax array (bool) of shape (num_nodes, num_nodes):
            adjacency matrix of the graph.
        - positions: jax array (int) of shape (num_agents,):
            the index of the last visited node.
        - action_mask: jax array (bool) of shape (num_agent, num_nodes):
            binary mask (False/True <--> invalid/valid action).

    - reward: float

    - action: jax array (int) of shape (num_agents,): [0,1,..., num_nodes-1]
        Each agent selects the next node to which it wants to connect.

    - state: State
        - node_type: jax array (int) of shape (num_nodes,).
            the component type of each node (-1 represents utility nodes).
        - adj_matrix: jax array (bool) of shape (num_nodes, num_nodes):
            adjacency matrix of the graph.
        - connected_nodes: jax array (int) of shape (num_agents, step_limit).
            we only count each node visit once.
        - connected_nodes_index: jax array (int) of shape (num_agents, num_nodes).
        - position_index: jax array (int) of shape (num_agents,).
        - node_edges: jax array (int) of shape (num_agents, num_nodes, num_nodes).
        - positions: jax array (int) of shape (num_agents,).
            the index of the last visited node.
        - action_mask: jax array (bool) of shape (num_agent, num_nodes).
            binary mask (False/True <--> invalid/valid action).
        - finished_agents: jax array (bool) of shape (num_agent,).
        - nodes_to_connect: jax array (int) of shape (num_agents, num_nodes_per_agent).
        - step_count: step counter.
        - step_limit: the number of steps allowed before an episode terminates.
        - key: PRNG key for random sample.

    - constants definitions:
        - Nodes
            - INVALID_NODE = -1: used to check if an agent selects an invalid node.
                A node may be invalid if its has no edge with the current node or if it is a
                utility node already selected by another agent.
            - UTILITY_NODE = -1: utility node (belongs to no agent).
            - EMPTY_NODE = -1: used for padding.
                state.connected_nodes stores the path (all the nodes) visited by an agent. Hence
                it has size equal to the step limit. We use this constant to initialise this array
                since 0 represents the first node.
            - DUMMY_NODE = -10: used for tie-breaking if multiple agents select the same node.

        - Edges
            - EMPTY_EDGE = -1: used for masking edges array.
               state.node_edges is the graph's adjacency matrix, but we don't represent it
               using 0s and 1s, we use the node values instead, i.e `A_ij = j` or `A_ij = -1`.
               Also edges are masked when utility nodes
               are selected by an agent to make it unaccessible by other agents.

        - Actions encoding
            - INVALID_CHOICE = -1
            - INVALID_TIE_BREAK = -2
            - INVALID_ALREADY_TRAVERSED = -3
    """

    def __init__(
        self,
        num_nodes: int = 12,
        num_edges: int = 24,
        max_degree: int = 5,
        num_agents: int = 2,
        num_nodes_per_agent: int = 3,
        step_limit: int = 70,
        reward_fn: Optional[RewardFn] = None,
        reward_values: Tuple = (0.1, -0.03, -0.01),  # (5.0, -0.2, -0.1)
        generator_fn: Type[Generator] = SplitRandomGenerator,
        renderer: Optional[Renderer] = None,
    ):
        """Create the Cooperative Minimum Spanning Tree environment.

        Args:
            num_nodes: number of nodes in the graph.
            num_edges: number of edges in the graph.
            max_degree: highest degree a node can have.
            num_agents: number of agents.
            num_nodes_per_agent: number of nodes to connect by each agent.
            step_limit: the number of steps allowed before an episode terminates.
            reward_fn: reward function.
            reward_values: reward values to use if we the default reward function.
                This is an array with 3 values. The first element is the reward
                for connection, the second is reward for no conncection and the last
                is the reward for and invalid choice.
            generator_fn: environment generator.
            renderer: envirnonment viewer.
        """

        if num_nodes_per_agent * num_agents > num_nodes * 0.8:
            raise ValueError(
                f"The number of nodes to connect i.e. {num_nodes_per_agent * num_agents} "
                f"should be much less than the number of nodes, which is {int(0.8*num_nodes)}."
            )

        self.num_nodes = num_nodes
        self.num_edges = num_edges
        self.num_agents = num_agents
        self.num_nodes_per_agent = num_nodes_per_agent
        self.max_degree = max_degree

        self._step_limit = step_limit
        self._reward_fn = reward_fn or DefaultRewardFn(
            reward_values=tuple(reward_values)
        )

        self._generator_fn = generator_fn(
            num_nodes,
            num_edges,
            max_degree,
            num_agents,
            num_nodes_per_agent,
            step_limit,
        )

        self._renderer = renderer

    def __repr__(self) -> str:
        return (
            f"CMST(num_nodes={self.num_nodes}, num_edges={self.num_edges}, "
            f"num_agents={self.num_agents}, num_components={self.num_nodes_per_agent})"
            f"max_degree={self.max_degree}"
        )

    def action_spec(self) -> specs.MultiDiscreteArray:
        """Returns the action spec.

        Returns:
            action_spec: a `specs.MultiDiscreteArray` spec.
        """
        return specs.MultiDiscreteArray(
            num_values=jnp.full((self.num_agents,), self.num_nodes, jnp.int32),
            name="action",
        )

    def observation_spec(self) -> specs.Spec[Observation]:
        """Returns the observation spec.

        Returns:
            observation_spec: a Tuple containing the spec for each of the constituent fields of an
            observation.
        """
        node_types = specs.BoundedArray(
            shape=(self.num_nodes,),
            minimum=-1,
            maximum=self.num_agents * 2 - 1,
            dtype=jnp.int32,
            name="node_types",
        )
        adj_matrix = specs.BoundedArray(
            shape=(self.num_nodes, self.num_nodes),
            minimum=0,
            maximum=1,
            dtype=jnp.int32,
            name="adj_matrix",
        )
        positions = specs.BoundedArray(
            shape=(self.num_agents,),
            minimum=-1,
            maximum=self.num_nodes - 1,
            dtype=jnp.int32,
            name="positions",
        )
        action_mask = specs.BoundedArray(
            shape=(self.num_agents, self.num_nodes),
            dtype=bool,
            minimum=False,
            maximum=True,
            name="action_mask",
        )

        return specs.Spec(
            Observation,
            "ObservationSpec",
            node_types=node_types,
            adj_matrix=adj_matrix,
            positions=positions,
            action_mask=action_mask,
        )

    def reset(self, key: chex.PRNGKey) -> Tuple[State, TimeStep]:
        """Resets the environment.

        Args:
            key: used to randomly generate the problem and the different start nodes.

        Returns:
             state: State object corresponding to the new state of the environment.
             timestep: TimeStep object corresponding to the first timestep returned by the
             environment.
        """

        key, problem_key = jax.random.split(key)

        (
            node_types,
            adj_matrix,
            agents_pos,
            conn_nodes,
            conn_nodes_index,
            node_edges,
            nodes_to_connect,
        ) = self._generator_fn(problem_key)

        active_node_edges = jnp.repeat(node_edges[None, ...], self.num_agents, axis=0)
        active_node_edges = self._update_active_edges(
            active_node_edges, agents_pos, node_types
        )
        finished_agents = jnp.zeros((self.num_agents), dtype=bool)

        state = State(
            node_types=node_types,
            adj_matrix=adj_matrix,
            nodes_to_connect=nodes_to_connect,
            connected_nodes=conn_nodes,
            connected_nodes_index=conn_nodes_index,
            position_index=jnp.zeros((self.num_agents), dtype=jnp.int32),
            positions=agents_pos,
            node_edges=active_node_edges,
            action_mask=self._update_action_mask(
                active_node_edges, agents_pos, finished_agents
            ),
            finished_agents=finished_agents,
            step_count=jnp.array(0, int),
            key=key,
        )

        timestep = restart(observation=self._state_to_observation(state))
        return state, timestep

    def step(self, state: State, action: chex.Array) -> Tuple[State, TimeStep]:
        """Run one timestep of the environment's dynamics.

        Args:
            state: State object containing the dynamics of the environment.
            action: Array containing the index of the next node to visit.

        Returns:
            state, timestep: Tuple[State, TimeStep] containing the next state of the environment,
            as well as the timestep to be observed.
        """

        def step_agent_fn(
            connected_nodes: chex.Array,
            conn_index: chex.Array,
            action: chex.Array,
            node: int,
            indices: chex.Array,
            agent_id: int,
        ) -> Tuple[chex.Array, ...]:

            is_invalid_choice = jnp.sum(action == INVALID_CHOICE) | jnp.sum(
                action == INVALID_TIE_BREAK
            )
            is_valid = (is_invalid_choice == 0) & (node != INVALID_NODE)
            connected_nodes, conn_index, new_node, indices = jax.lax.cond(
                is_valid,
                self._update_conected_nodes,
                lambda *_: (
                    connected_nodes,
                    conn_index,
                    state.positions[agent_id],
                    indices,
                ),
                connected_nodes,
                conn_index,
                node,
                indices,
            )

            return connected_nodes, conn_index, new_node, indices

        key, step_key = jax.random.split(state.key)
        action, next_nodes = self._trim_duplicated_invalid_actions(
            state, action, step_key
        )

        connected_nodes = jnp.zeros_like(state.connected_nodes)
        connected_nodes_index = jnp.zeros_like(state.connected_nodes_index)
        agents_pos = jnp.zeros_like(state.positions)
        position_index = jnp.zeros_like(state.position_index)

        for agent in range(self.num_agents):
            conn_nodes_i, conn_nodes_id, pos_i, pos_ind = step_agent_fn(
                state.connected_nodes[agent],
                state.connected_nodes_index[agent],
                action[agent],
                next_nodes[agent],
                state.position_index[agent],
                agent,
            )

            connected_nodes = connected_nodes.at[agent].set(conn_nodes_i)
            connected_nodes_index = connected_nodes_index.at[agent].set(conn_nodes_id)
            agents_pos = agents_pos.at[agent].set(pos_i)
            position_index = position_index.at[agent].set(pos_ind)

        active_node_edges = self._update_active_edges(
            state.node_edges, agents_pos, state.node_types
        )

        state = State(
            node_types=state.node_types,
            adj_matrix=state.adj_matrix,
            nodes_to_connect=state.nodes_to_connect,
            connected_nodes=connected_nodes,
            connected_nodes_index=connected_nodes_index,
            position_index=position_index,
            positions=agents_pos,
            node_edges=active_node_edges,
            action_mask=self._update_action_mask(
                active_node_edges, agents_pos, state.finished_agents
            ),
            finished_agents=state.finished_agents,  # not updated yet
            step_count=state.step_count,
            key=key,
        )

        state, timestep = self._state_to_timestep(state, action)
        return state, timestep

    def _state_to_observation(self, state: State) -> Observation:
        """Converts a state into an observation.

        Args:
            state: State object containing the dynamics of the environment.

        Returns:
            observation: Observation object containing the observation of the environment.
        """

        node_types = self._get_obsv(state.node_types, state.connected_nodes_index)

        return Observation(
            node_types=node_types,
            adj_matrix=state.adj_matrix,
            positions=state.positions,
            action_mask=state.action_mask,
        )

    def _state_to_timestep(
        self, state: State, action: chex.Array
    ) -> Tuple[State, TimeStep]:
        """Checks if the state is terminal and converts it into a timestep.

        Args:
            state: State object containing the dynamics of the environment.
            action: action taken the agent in this step.

        Returns:
            timestep: TimeStep object containing the timestep of the environment.
        """

        observation = self._state_to_observation(state)
        finished_agents = self.get_finished_agents(state)
        rewards = (
            self._reward_fn(state, action, state.nodes_to_connect)
            * ~state.finished_agents
        )

        # sum the rewards to make the environment is single agent
        reward = jnp.sum(rewards)

        # update the state now
        state.finished_agents = finished_agents
        state.step_count = state.step_count + 1

        def make_termination_timestep(state: State) -> TimeStep:
            return termination(
                reward=reward,
                observation=observation,
            )

        def make_truncation_timestep(state: State) -> TimeStep:
            return truncation(
                reward=reward,
                observation=observation,
            )

        def make_transition_timestep(state: State) -> TimeStep:
            return transition(
                reward=reward,
                observation=observation,
            )

        is_done = finished_agents.all()
        horizon_reached = state.step_count >= self._step_limit

        # false + false = 0 = transition
        # true + false = 1  = truncation
        # false + true * 2 = 2 = termination
        # true + true * 2 = 3 -> gets clamped to 2 = termination
        timestep: TimeStep[chex.Array] = jax.lax.switch(
            horizon_reached + is_done * 2,
            [
                make_transition_timestep,
                make_truncation_timestep,
                make_termination_timestep,
            ],
            state,
        )

        return state, timestep

    def _get_obsv(
        self,
        node_types: chex.Array,
        connected_nodes_index: chex.Array,
    ) -> chex.Array:
        """Encodes the node_types with respect.

        Args:
            node_types: the environment state node_types.
            connected_nodes_index: nodes already connected to this component (index view)
        Returns:
            Array: the state in the perspective of the agent.
        """

        # Each agent should see its note_types labelled with id 1
        # and all its already connected nodes labeled with id 0

        # to presever the negative ones
        zero_mask = node_types != UTILITY_NODE
        ones_inds = node_types == UTILITY_NODE

        # set the agent_id to 0 since the environment is now single agent.
        agent_id = 0

        node_types -= agent_id
        node_types %= self.num_agents
        node_types *= 2
        node_types += 1  # add one so that current agent nodes are labelled 1

        node_types *= zero_mask  # masking the position with negative ones
        node_types -= ones_inds  # adding negative ones back

        # set already connected nodes by agent to 0 #
        for agent in range(self.num_agents):
            connected_mask = connected_nodes_index[agent] == UTILITY_NODE
            connected_ones = connected_nodes_index[agent] != UTILITY_NODE
            node_types *= connected_mask
            agent_skip = (agent - agent_id) % self.num_agents
            node_types += (2 * agent_skip) * connected_ones

        return node_types

    def _update_action_mask(
        self, node_edges: chex.Array, position: chex.Array, finished_agents: chex.Array
    ) -> chex.Array:
        """Intialise and action mask for every node based on all its edges

        Args:
            node_edges (Array): Array with the respective edges for
                every node (-1 for invalid edge)
            position: current node of each agent
            finished_agents: (Array): used to mask finished agents
        Returns:
            action_mask (Array): action mask for each agent at it current node position
        """

        full_action_mask = node_edges != EMPTY_NODE
        action_mask = jnp.zeros((self.num_agents, self.num_nodes), dtype=bool)
        for agent in range(self.num_agents):
            node = position[agent]
            action_mask = action_mask.at[agent].set(
                full_action_mask[agent, node] * ~finished_agents[agent]
            )

        return action_mask

    def _update_active_edges(
        self, node_edges: chex.Array, position: chex.Array, node_types: chex.Array
    ) -> chex.Array:
        """Update the active agent nodes available to each agent

        Args:
            node_edges: (array) with node edges
            position: (array) for current agent position
            node_types: array
        Returns:
            active_node_edges: (array)
        """

        def update_edges(node_edges: chex.Array, node: jnp.int32) -> chex.Array:
            zero_mask = node_edges != node
            ones_inds = node_edges == node
            upd_edges = node_edges * zero_mask - ones_inds
            return upd_edges

        active_node_edges = jnp.copy(node_edges)

        for agent in range(self.num_agents):
            node = position[agent]
            cond = node_types[node] == UTILITY_NODE

            for agent2 in range(self.num_agents):
                if agent != agent2:
                    upd_edges = jax.lax.cond(
                        cond,
                        update_edges,
                        lambda _edgs, _node: _edgs,
                        active_node_edges[agent2],
                        node,
                    )
                    active_node_edges = active_node_edges.at[agent2].set(upd_edges)

        return active_node_edges

    def _trim_duplicated_invalid_actions(
        self, state: State, action: chex.Array, step_key: chex.PRNGKey
    ) -> chex.Array:
        """Check for duplicated actions and randomly break ties.

        Args:
            state: State object containing the dynamics of the environment.
            action: Array containing the index of the next node to visit.
        Returns:
            action: Array containing the index of the next node to visit.
                -2 indicates do not move because of tie break
                -1 indicates do not move because of an invalid choice
                -3 indicates moving to an already traversed node
            nodes: actual new nodes
                -1 invalid node no movement
        """

        def _get_agent_node(
            node_edges: chex.Array, position: chex.Array, action: chex.Array
        ) -> chex.Array:
            node = node_edges[position, action]
            return node

        nodes = jax.vmap(_get_agent_node)(state.node_edges, state.positions, action)

        new_actions = jnp.ones_like(action) * INVALID_CHOICE

        added_nodes = jnp.ones((self.num_agents), dtype=jnp.int32) * DUMMY_NODE

        agent_permutation = jax.random.permutation(
            step_key, jnp.arange(self.num_agents)
        )

        def not_all_agents_actions_examined(arg: Any) -> Any:
            added_nodes, new_actions, action, nodes, agent_permutation, index = arg
            return index < self.num_agents

        def modify_action_if_agent_target_node_is_selected(arg: Any) -> Any:
            added_nodes, new_actions, action, nodes, agent_permutation, index = arg
            agent_i = agent_permutation[index]

            is_invalid_node = nodes[agent_i] == EMPTY_NODE
            node_is_not_selected = jnp.sum(jnp.sum(added_nodes == nodes[agent_i]) == 0)

            # false + false = 0 = tie break
            # true + false = 1  = invalid choice (with tie break) do nothing
            # false + true * 2 = 2 = valid choice and valid node
            # true + true * 2 = 3 -> invalid choice (without tie break)

            new_actions, added_nodes = jax.lax.switch(
                is_invalid_node + node_is_not_selected * 2,
                [
                    lambda *_: (
                        new_actions.at[agent_i].set(INVALID_TIE_BREAK),
                        added_nodes.at[agent_i].set(INVALID_TIE_BREAK),
                    ),
                    lambda *_: (new_actions, added_nodes),
                    lambda *_: (
                        new_actions.at[agent_i].set(action[agent_i]),
                        added_nodes.at[agent_i].set(nodes[agent_i]),
                    ),
                    lambda *_: (new_actions, added_nodes),
                ],
                new_actions,
                added_nodes,
            )
            index += 1

            return (added_nodes, new_actions, action, nodes, agent_permutation, index)

        (
            added_nodes,
            new_actions,
            action,
            nodes,
            agent_permutation,
            index,
        ) = jax.lax.while_loop(
            not_all_agents_actions_examined,
            modify_action_if_agent_target_node_is_selected,
            (added_nodes, new_actions, action, nodes, agent_permutation, 0),
        )

        def mask_visited_nodes(
            node_visited: jnp.int32, oldaction: jnp.int32
        ) -> jnp.int32:
            new_action = jax.lax.cond(  # type:ignore
                node_visited != EMPTY_NODE,
                lambda *_: INVALID_ALREADY_TRAVERSED,
                lambda *_: oldaction,
            )

            return new_action

        final_actions = jnp.zeros_like(new_actions)
        # set the action to 0 if the agent is moving to an already connected node
        for agent in range(self.num_agents):
            node_visited = state.connected_nodes_index[agent, nodes[agent]]
            new_action = mask_visited_nodes(node_visited, new_actions[agent])
            final_actions = final_actions.at[agent].set(new_action)

        # masked agents with finished states
        final_actions = final_actions * ~state.finished_agents - state.finished_agents

        return final_actions, nodes

    def _update_conected_nodes(
        self,
        connected_nodes: chex.Array,
        connected_node_index: chex.Array,
        node: int,
        index: int,
    ) -> chex.Array:
        """Add this node to the connected_nodes part of the specific agent

        Args:
            connected_nodes (Array): Nodes connected by each agent.
            connected_nodes_index (Array): Nodes connected by each agent.
            node (int): New node to connect
            index (int): position
        Returns:
            connected_nodes (Array): Array with connected node appended.
        """

        index += 1
        connected_nodes = connected_nodes.at[index].set(node)
        connected_node_index = connected_node_index.at[node].set(node)
        return connected_nodes, connected_node_index, node, index

    def get_finished_agents(self, state: State) -> chex.Array:
        """Get the done flags for each agent.

        Args:
            node_types: the environment state node_types.
            connected_nodes: the agent specifc view of connected nodes
        Returns:
            Array : array of boolean flags in the shape (number of agents, ).
        """

        def done_fun(
            nodes: chex.Array, connected_nodes: chex.Array, n_comps: int
        ) -> jnp.bool_:
            connects = jnp.isin(nodes, connected_nodes)
            return jnp.sum(connects) == n_comps

        finished_agents = jnp.zeros_like(state.finished_agents)
        for agent in range(self.num_agents):
            finished = done_fun(
                state.nodes_to_connect[agent],
                state.connected_nodes[agent],
                self.num_nodes_per_agent,
            )
            finished_agents = finished_agents.at[agent].set(finished)

        return finished_agents

    def render(self, state: State) -> chex.Array:
        """Render the environment for a given state.

        Returns:
            Array of rgb pixel values in the shape (width, height, rgb).
        """
        if self._renderer is None:
            self._renderer = Renderer(
                self.num_agents,
                state.nodes_to_connect,
                num_nodes=self.num_nodes,
                adj_matrix=state.adj_matrix,
            )

        return self._renderer.render(state)

    def animate(
        self,
        states: Sequence[State],
        interval: int = 200,
        save_path: Optional[str] = None,
    ) -> None:
        """Calls the environment renderer to animate a sequence of states.

        Args:
            states: List of states to animate.
            interval: Time between frames in milliseconds.
            save_path: Optional path to save the animation.
        """
        if self._renderer is None:
            self._renderer = Renderer(
                self.num_agents,
                states[0].nodes_to_connect,
                num_nodes=self.num_nodes,
                adj_matrix=states[0].adj_matrix,
            )
        self._renderer.animate(states, interval, save_path)
