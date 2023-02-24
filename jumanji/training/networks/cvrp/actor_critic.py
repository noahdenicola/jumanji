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

import chex
import haiku as hk
import jax.numpy as jnp

from jumanji.environments.routing.cvrp import CVRP, Observation
from jumanji.environments.routing.cvrp.constants import DEPOT_IDX
from jumanji.training.networks.actor_critic import (
    ActorCriticNetworks,
    FeedForwardNetwork,
)
from jumanji.training.networks.encoder_decoder import (
    CriticDecoderBase,
    EncoderBase,
    PolicyDecoderBase,
)
from jumanji.training.networks.parametric_distribution import (
    CategoricalParametricDistribution,
)


def make_actor_critic_networks_cvrp(
    cvrp: CVRP,
    encoder_num_layers: int,
    encoder_num_heads: int,
    encoder_key_size: int,
    encoder_model_size: int,
    encoder_expand_factor: int,
    decoder_num_heads: int,
    decoder_key_size: int,
    decoder_model_size: int,
) -> ActorCriticNetworks:
    """Make actor-critic networks for CVRP."""
    num_actions = cvrp.action_spec().num_values
    parametric_action_distribution = CategoricalParametricDistribution(
        num_actions=num_actions
    )
    policy_network = make_network_cvrp(
        num_outputs=num_actions,
        encoder_num_layers=encoder_num_layers,
        encoder_num_heads=encoder_num_heads,
        encoder_key_size=encoder_key_size,
        encoder_model_size=encoder_model_size,
        encoder_expand_factor=encoder_expand_factor,
        decoder_num_heads=decoder_num_heads,
        decoder_key_size=decoder_key_size,
        decoder_model_size=decoder_model_size,
    )
    value_network = make_network_cvrp(
        num_outputs=1,
        encoder_num_layers=encoder_num_layers,
        encoder_num_heads=encoder_num_heads,
        encoder_key_size=encoder_key_size,
        encoder_model_size=encoder_model_size,
        encoder_expand_factor=encoder_expand_factor,
        decoder_num_heads=decoder_num_heads,
        decoder_key_size=decoder_key_size,
        decoder_model_size=decoder_model_size,
    )
    return ActorCriticNetworks(
        policy_network=policy_network,
        value_network=value_network,
        parametric_action_distribution=parametric_action_distribution,
    )


def make_network_cvrp(
    num_outputs: int,
    encoder_num_layers: int,
    encoder_num_heads: int,
    encoder_key_size: int,
    encoder_model_size: int,
    encoder_expand_factor: int,
    decoder_num_heads: int,
    decoder_key_size: int,
    decoder_model_size: int,
) -> FeedForwardNetwork:
    def network_fn(
        observation: Observation,
    ) -> chex.Array:
        encoder = Encoder(
            num_layers=encoder_num_layers,
            num_heads=encoder_num_heads,
            key_size=encoder_key_size,
            model_size=encoder_model_size,
            expand_factor=encoder_expand_factor,
        )
        problem = jnp.concatenate(
            [observation.coordinates, observation.demands[:, :, None]], axis=-1
        )
        embedding = encoder(problem)
        if num_outputs == 1:
            decoder = CriticDecoder(
                num_heads=decoder_num_heads,
                key_size=decoder_key_size,
                model_size=decoder_model_size,
            )
        else:
            decoder = PolicyDecoder(
                num_heads=decoder_num_heads,
                key_size=decoder_key_size,
                model_size=decoder_model_size,
            )
        return decoder(observation, embedding)

    init, apply = hk.without_apply_rng(hk.transform(network_fn))
    return FeedForwardNetwork(init=init, apply=apply)


class Encoder(EncoderBase):
    def __init__(
        self,
        num_layers: int,
        num_heads: int,
        key_size: int,
        model_size: int,
        expand_factor: int,
    ):
        super().__init__(num_layers, num_heads, key_size, model_size, expand_factor)

    def get_problem_projection(self, problem: chex.Array) -> chex.Array:
        """Modified for CVRP according to original source code:
        https://github.com/yd-kwon/POMO/blob/master/NEW_py_ver/CVRP/POMO/CVRPModel.py
        (~line 116).
        """
        proj_depot = hk.Linear(self.model_size, name="depot_encoder")
        proj_nodes = hk.Linear(self.model_size, name="nodes_encoder")
        return jnp.where(
            jnp.zeros(problem.shape[:-1], bool).at[:, DEPOT_IDX].set(True)[:, :, None],
            proj_depot(problem),
            proj_nodes(problem),
        )


def get_context(observation: Observation, embeddings: chex.Array) -> chex.Array:
    nodes_embedding = jnp.mean(embeddings, axis=-2)
    position_embedding = jnp.take_along_axis(
        embeddings, observation.position[:, None, None], axis=-2
    ).squeeze(axis=-2)
    position_embedding = jnp.where(
        observation.position[:, None] == -1,
        jnp.zeros_like(nodes_embedding),
        position_embedding,
    )
    return jnp.concatenate(
        [
            nodes_embedding,
            position_embedding,
            observation.capacity[:, None],
        ],
        axis=-1,
    )[:, None, :]


class PolicyDecoder(PolicyDecoderBase):
    def __init__(self, num_heads: int, key_size: int, model_size: int):
        super().__init__(num_heads, key_size, model_size)

    def get_context(  # type: ignore[override]
        self, observation: Observation, embeddings: chex.Array
    ) -> chex.Array:
        return get_context(observation, embeddings)

    def get_transformed_attention_mask(self, attention_mask: chex.Array) -> chex.Array:
        return attention_mask


class CriticDecoder(CriticDecoderBase):
    def __init__(self, num_heads: int, key_size: int, model_size: int):
        super().__init__(num_heads, key_size, model_size)

    def get_context(  # type: ignore[override]
        self, observation: Observation, embeddings: chex.Array
    ) -> chex.Array:
        return get_context(observation, embeddings)

    def get_transformed_attention_mask(self, attention_mask: chex.Array) -> chex.Array:
        return attention_mask
