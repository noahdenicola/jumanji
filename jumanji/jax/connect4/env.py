from typing import Tuple

from chex import Array, PRNGKey
from dm_env import specs
from jax import lax
from jax import numpy as jnp

from jumanji.jax import JaxEnv
from jumanji.jax.connect4.constants import BOARD_HEIGHT, BOARD_WIDTH
from jumanji.jax.connect4.types import Observation, State
from jumanji.jax.connect4.utils import (
    board_full,
    get_action_mask,
    get_highest_row,
    is_winning,
    update_board,
)
from jumanji.jax.types import Action, Extra, TimeStep, restart, termination, transition


class Connect4(JaxEnv[State]):
    """A JAX implementation of the 'Connect 4' game.

    - observation: a dataclass with two attributes:
        - board: jax array (int8) of shape (6, 7):
            each cell contains either:
            - 1 if it contains a token by the current player,
            - 0 if it is free,
            - (-1) if it contains a token by the other player.
        - action_mask: jax array (int8)
            valid columns (actions) are identified with 1, invalid ones with 0.

    - reward: jax array (float) of shape (2,):
        1 for the winning player, 0 for a draw, -1 for the loosing player.

    - episode termination:
        - if the board is full, the game ends on a draw,
        - if a player connects 4 tokens (horizontally, vertically or diagonally), they win
        and the game ends.
        - if a player plays an invalid move, this player loses and the game ends.

    - state: State:
        - current_player: int, id of the current player {0, 1}.
        - board: jax array (int8) of shape (6, 7):
            each cell contains either:
            - 1 if it contains a token placed by the current player,
            - 0 if it is free,
            - (-1) if it contains a token placed by the other player.

    """

    n_players: int = 2

    def reset(self, key: PRNGKey) -> Tuple[State, TimeStep[Observation], Extra]:
        """Resets the environment.

        Args:
            key: not used.

        Returns:
            state: State object corresponding to the new state of the environment,
            timestep: TimeStep object corresponding the first timestep returned by the environment,
            extra: metrics, contains the current player.
        """
        del key
        board = jnp.zeros((BOARD_HEIGHT, BOARD_WIDTH), dtype=jnp.int8)
        action_mask = jnp.ones((BOARD_WIDTH,), dtype=jnp.int8)

        obs = Observation(board=board, action_mask=action_mask)

        timestep = restart(observation=obs, shape=(self.n_players,))

        state = State(current_player=jnp.int8(0), board=board)
        extra = {"current_player": jnp.array(0, dtype=jnp.int8)}
        return state, timestep, extra

    def step(
        self, state: State, action: Action
    ) -> Tuple[State, TimeStep[Observation], Extra]:
        """Run one timestep of the environment's dynamics.

        Args:
            state: State object containing the dynamics of the environment.
            action: Array containing the column to insert the token into {0, 1, 2, 3, 4, 5, 6}

        Returns:
            state: State object corresponding to the next state of the environment,
            timestep: TimeStep object corresponding the timestep returned by the environment,
            extra: metrics, contains the current player.
        """
        board = state.board

        # getting the height of the column
        highest_row = get_highest_row(board[:, action])

        # checking the validity of the move
        invalid = jnp.any(highest_row == 0)

        # applying action
        new_board = lax.cond(
            invalid, lambda x: x, lambda b: update_board(b, highest_row, action), board
        )

        # computing winning condition
        winning = is_winning(new_board)

        # computing terminal condition
        done = invalid | winning | board_full(new_board)

        # computing action mask
        action_mask = get_action_mask(new_board)

        # switching player
        next_player = (state.current_player + 1) % self.n_players

        # computing reward
        reward_value = compute_reward(invalid, winning)

        reward = jnp.zeros((self.n_players,))
        reward = reward.at[state.current_player].set(reward_value)

        # opponent gets the opposite reward of the current player
        reward = reward.at[next_player].set(-reward_value)

        # creating next state
        next_state = State(current_player=jnp.int8(next_player), board=new_board)

        obs = Observation(board=new_board, action_mask=action_mask)

        timestep = lax.cond(
            done,
            lambda _: termination(
                reward=reward, observation=obs, shape=(self.n_players,)
            ),
            lambda _: transition(
                reward=reward, observation=obs, shape=(self.n_players,)
            ),
            operand=None,
        )

        extra = {"current_player": next_player}

        return next_state, timestep, extra

    def observation_spec(self) -> specs.Array:
        """Returns the observation spec.

        Returns:
            observation_spec: dm_env.specs object
        """
        return specs.Array(shape=(6, 7), dtype=jnp.int8, name="observation")

    def action_spec(self) -> specs.Array:
        """Returns the action spec. 7 actions: [0,1,2,3,4,5,6] -> one per column.

        Returns:
            action_spec: dm_env.specs object
        """
        return specs.DiscreteArray(7, name="action")

    @staticmethod
    def render(state: State) -> str:
        """Renders a given state.

        Args:
            state: State object corresponding to the new state of the environment.

        Returns:
            human-readable string displaying the current state of the game.

        """
        message = f"Current player: {state.current_player}\n"
        message += f"Board: \n {str(state.board)}"
        return message


def compute_reward(invalid: Array, winning: Array) -> Array:
    """Computes the reward based on the validity of the move of the current player and whether it
    was a winning move or not.

    Reward is as follows:
        - if the move was a winning move then the player receives 1,
        - if it was an invalid move, the player receives -1.
        - otherwise, the player receives 0.

    Args:
        invalid: True means the move was not valid,
        winning: True means the move was a winning move.

    Returns:
        The reward
    """
    reward = lax.cond(winning, lambda _: 1, lambda _: 0, operand=None)
    return lax.cond(invalid, lambda _: -1, lambda r: r, operand=reward)
