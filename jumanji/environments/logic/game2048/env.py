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

from typing import Tuple

import jax
import jax.numpy as jnp
from chex import Array, PRNGKey

from jumanji import specs
from jumanji.env import Environment
from jumanji.environments.logic.game2048.specs import ObservationSpec
from jumanji.environments.logic.game2048.types import Board, Observation, State
from jumanji.environments.logic.game2048.utils import (
    move_down,
    move_left,
    move_right,
    move_up,
)
from jumanji.types import Action, TimeStep, restart, termination, transition


class Game2048(Environment[State]):
    """The Game2048 class represents an environment for the game 2048. It has a board of size
    board_size x board_size (4x4 by default), and the player can take actions to move the tiles
    on the board up, down, left, or right. The goal of the game is to combine tiles with the
    same number to create a tile with double the value, until the player at least reaches a
    tile with the value 2048 to consider it as a win.

    The environment has the following characteristics:
    - observation: `Observation`
        - board: a 2D array representing the current state of the board. An empty tile is
        represented by zero whereas a non-empty tile is an exponent of 2, for example 1, 2,
        3, 4, ... (corresponding to 2, 4, 8, 16, ...).
        - action_mask: a 1D boolean array indicating which actions are valid in the current state of
        the environment. The actions are up, right, down, and left.

    - action: a discrete value in the range [0, 3], representing the actions up, right, down,
    and left, respectively.

    - reward: the reward is 0 except when the player combines tiles to create a new tile with twice
    the value. In this case, the reward is the value of the new tile.

    - episode termination: when no more valid moves exist (this can happen when the board is full).
    """

    def __init__(self, board_size: int = 4) -> None:
        """Initialize the 2048 game.

        Args:
            board_size: size of the board (default: 4).
        """
        self.board_size = board_size

    def __repr__(self) -> str:
        """String representation of the environment.

        Returns:
            str: the string representation of the environment.
        """
        return f"2048 Game(board_size={self.board_size})"

    def observation_spec(self) -> ObservationSpec:
        """Returns the observation spec containing the board and action_mask arrays.

        Returns:
            observation_spec: `ObservationSpec` tree of board and action_mask spec.
        """
        return ObservationSpec(
            board_spec=specs.Array(
                shape=(self.board_size, self.board_size), dtype=jnp.int32, name="board"
            ),
            action_mask_spec=specs.BoundedArray(
                shape=(4,),
                dtype=bool,
                minimum=False,
                maximum=True,
                name="action_mask",
            ),
        )

    def action_spec(self) -> specs.DiscreteArray:
        """Returns the action spec.

        4 actions: [0, 1, 2, 3] -> [Up, Right, Down, Left].

        Returns:
            action_spec: `DiscreteArray` spec object.
        """
        return specs.DiscreteArray(4, name="action")

    def reset(self, key: PRNGKey) -> Tuple[State, TimeStep[Observation]]:
        """Resets the environment.

        Args:
            key: random number generator key.

        Returns:
            state: the new state of the environment.
            timestep: the first timestep returned by the environment.
        """

        key, board_key = jax.random.split(key)
        board = self._generate_board(board_key)
        action_mask = self._get_action_mask(board)

        obs = Observation(board=board, action_mask=action_mask)

        timestep = restart(observation=obs)

        state = State(
            board=board, step_count=jnp.int32(0), action_mask=action_mask, key=key
        )

        return state, timestep

    def step(self, state: State, action: Action) -> Tuple[State, TimeStep[Observation]]:
        """Updates the environment state after the agent takes an action.

        Args:
            state: the current state of the environment.
            action: the action taken by the agent.

        Returns:
            state: the new state of the environment.
            timestep: the next timestep.
        """
        # Take the action in the environment: Up, Right, Down, Left.
        updated_board, additional_reward = jax.lax.switch(
            action,
            [move_up, move_right, move_down, move_left],
            state.board,
        )

        # Generate action mask to keep in the state for the next step and
        # to provide to the agent in the observation.
        action_mask = self._get_action_mask(board=updated_board)

        # Check if the episode terminates (i.e. there are no legal actions).
        done = ~jnp.any(action_mask)

        # Generate new key.
        random_cell_key, new_state_key = jax.random.split(state.key)

        # Update the state of the board by adding a new random cell.
        updated_board = jax.lax.cond(
            done,
            lambda board, key: updated_board,
            self._add_random_cell,
            updated_board,
            random_cell_key,
        )

        # Build the state.
        state = State(
            board=updated_board,
            action_mask=action_mask,
            step_count=state.step_count + 1,
            key=new_state_key,
        )

        # Generate the observation from the environment state.
        observation = Observation(
            board=updated_board,
            action_mask=action_mask,
        )

        # Return either a MID or a LAST timestep depending on done.
        timestep = jax.lax.cond(
            done,
            termination,
            transition,
            additional_reward,
            observation,
        )

        return state, timestep

    def _generate_board(self, key: PRNGKey) -> Board:
        """Generates an initial board for the environment.

        The method generates an empty board with the specified size and fills a random cell with
        a value of 1 or 2 representing the exponent of 2.

        Args:
            key: random number generator key.

        Returns:
            board: initial board for the environment.
        """
        # Create empty board
        board = jnp.zeros((self.board_size, self.board_size), dtype=jnp.int32)

        # Fill one random cell with a value of 1 or 2
        board = self._add_random_cell(board, key)

        return board

    def _add_random_cell(self, board: Board, key: PRNGKey) -> Board:
        """Adds a new random cell to the board.

        This method selects an empty position in the board and assigns it a value
        of 1 or 2 representing the exponent of 2.

        Args:
            board: current board of the environment.
            key: random number generator key.

        Returns:
            board: updated board with the new random cell added.
        """
        key, subkey = jax.random.split(key)

        # Select position of the new random cell
        empty_flatten_board = jnp.ravel(board == 0)
        tile_idx = jax.random.choice(
            key, jnp.arange(len(empty_flatten_board)), p=empty_flatten_board
        )
        # Convert the selected tile's location in the flattened array to its position on the board.
        position = jnp.divmod(tile_idx, self.board_size)

        # Choose the value of the new cell: 1 with probability 90% or 2 with probability of 10%
        cell_value = jax.random.choice(
            subkey, jnp.array([1, 2]), p=jnp.array([0.9, 0.1])
        )
        board = board.at[position].set(cell_value)

        return board

    def _get_action_mask(self, board: Board) -> Array:
        """Generates a binary mask indicating which actions are valid.

        If the movement in that direction leaves the board unchanged, the action is
        considered illegal.

        Args:
            board: current board of the environment.

        Returns:
            action_mask: action mask for the current state of the environment.
        """
        action_mask = jnp.array(
            [
                jnp.any(move_up(board, final_shift=False)[0] != board),
                jnp.any(move_right(board, final_shift=False)[0] != board),
                jnp.any(move_down(board, final_shift=False)[0] != board),
                jnp.any(move_left(board, final_shift=False)[0] != board),
            ],
        )
        return action_mask
