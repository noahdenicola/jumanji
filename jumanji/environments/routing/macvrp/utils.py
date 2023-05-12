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

import chex
import jax

from jumanji.environments.routing.macvrp.constants import DEPOT_IDX


def compute_time_penalties(
    local_times: chex.Array,
    window_start: chex.Array,
    window_end: chex.Array,
    early_coefs: chex.Array,
    late_coefs: chex.Array,
) -> chex.Array:
    """Calculate the time penalties for this step.
    The time penalties are calculated as the sum of the early and late penalties.
    The early penalty is calculated as the difference between the local time and the window
        start time multiplied by the early coefficient. The late penalty is calculated as the
        difference between the window end time and the local time multiplied by the late
        coefficient.
    The early and late penalties are only calculated if the local time is outside the window.
    If the local time is inside the window, the early and late penalties are zero.

    """
    early_penalty = jax.numpy.where(
        local_times < window_start,
        (window_start - local_times) * early_coefs,
        0,
    )
    late_penalty = jax.numpy.where(
        local_times > window_end,
        (local_times - window_end) * late_coefs,
        0,
    )
    time_penalties = early_penalty + late_penalty

    return time_penalties


def compute_distance(
    start_node_coordinates: chex.Array, end_node_coordinates: chex.Array
) -> jax.numpy.float32:
    """Calculate the distance traveled between two nodes"""
    return jax.numpy.linalg.norm(
        (start_node_coordinates - end_node_coordinates), axis=1
    )


def generate_problem(
    key: chex.PRNGKey,
    num_customers: jax.numpy.int16,
    total_capacity: jax.numpy.int16,
    map_max: jax.numpy.float32,
    customer_demand_max: jax.numpy.int16,
    max_start_window: jax.numpy.float32,
    window_length: jax.numpy.float32,
    early_coef_rand: Tuple[jax.numpy.float32, jax.numpy.float32],
    late_coef_rand: Tuple[jax.numpy.float32, jax.numpy.float32],
) -> Tuple[chex.Array, chex.Array, chex.Array, chex.Array, chex.Array, chex.Array]:

    # Generate the node coordinates
    coord_key, demand_key, window_key, earl_key, late_key = jax.random.split(key, 5)

    node_coordinates = jax.random.uniform(
        coord_key, (num_customers + 1, 2), minval=0, maxval=map_max
    )

    node_demands = jax.random.randint(
        demand_key, (num_customers + 1,), minval=0, maxval=customer_demand_max
    )
    node_demands = node_demands.at[DEPOT_IDX].set(0)

    # The total demands are controlled to less than the total capacity of all
    # vehicles to ensure a feasible solution.
    node_demands = jax.numpy.asarray(
        node_demands * (total_capacity / jax.numpy.sum(node_demands)),
        dtype=jax.numpy.int16,
    )

    window_start_times = jax.random.uniform(
        window_key, (num_customers + 1,), minval=0, maxval=max_start_window
    )
    window_end_times = window_start_times + window_length

    earl_coef = jax.random.uniform(
        earl_key,
        (num_customers + 1,),
        minval=early_coef_rand[0],
        maxval=early_coef_rand[1],
    )
    late_coef = jax.random.uniform(
        late_key,
        (num_customers + 1,),
        minval=late_coef_rand[0],
        maxval=late_coef_rand[1],
    )

    # No penalties for going to the depot
    earl_coef = earl_coef.at[DEPOT_IDX].set(0.0)
    late_coef = late_coef.at[DEPOT_IDX].set(0.0)

    return (
        node_coordinates,
        node_demands,
        window_start_times,
        window_end_times,
        earl_coef,
        late_coef,
    )


def get_6_customer_init_settings(
    num_vehicles: int,
) -> Tuple[chex.Array, chex.Array, chex.Array, chex.Array, chex.Array, chex.Array]:
    if num_vehicles not in [2, 3]:
        raise ValueError("num_vehicles must be 2 or 3 for num_customers=6")
    """
        This example is not part of the original problem, but servers as an
            simpler environment to experiment with.
    """

    map_max = 10
    max_capacity = 20
    max_start_window = 10.0
    early_coef_rand = (0.0, 0.2)
    late_coef_rand = (0.0, 1.0)
    if num_vehicles == 2:
        customer_demand_max = 10
    else:
        customer_demand_max = 20

    return (
        map_max,
        max_capacity,
        max_start_window,
        early_coef_rand,
        late_coef_rand,
        customer_demand_max,
    )


def get_20_customer_init_settings(
    num_vehicles: int,
) -> Tuple[chex.Array, chex.Array, chex.Array, chex.Array, chex.Array, chex.Array]:
    if num_vehicles not in [2, 3]:
        raise ValueError("num_vehicles must be 2 or 3 for num_customers=20")
    map_max = 10
    max_capacity = 60
    max_start_window = 10.0
    early_coef_rand = (0.0, 0.2)
    late_coef_rand = (0.0, 1.0)
    if num_vehicles == 2:
        customer_demand_max = 10
    else:
        customer_demand_max = 15

    return (
        map_max,
        max_capacity,
        max_start_window,
        early_coef_rand,
        late_coef_rand,
        customer_demand_max,
    )


def get_50_customer_init_settings(
    num_vehicles: int,
) -> Tuple[chex.Array, chex.Array, chex.Array, chex.Array, chex.Array, chex.Array]:
    if num_vehicles not in [2, 3, 4, 5]:
        raise ValueError("num_vehicles must be 2, 3, 4 or 5 for num_customers=50")
    map_max = 20
    max_capacity = 150
    max_start_window = 20.0
    early_coef_rand = (0.0, 0.2)
    late_coef_rand = (0.0, 1.0)
    if num_vehicles == 2:
        customer_demand_max = 10
    elif num_vehicles == 3:
        customer_demand_max = 15
    elif num_vehicles == 4:
        customer_demand_max = 20
    else:
        customer_demand_max = 25
    return (
        map_max,
        max_capacity,
        max_start_window,
        early_coef_rand,
        late_coef_rand,
        customer_demand_max,
    )


def get_100_customer_init_settings(
    num_vehicles: int,
) -> Tuple[chex.Array, chex.Array, chex.Array, chex.Array, chex.Array, chex.Array]:
    if num_vehicles not in [2, 3, 4, 5]:
        raise ValueError("num_vehicles must be 2, 3, 4 or 5 for num_customers=50")
    map_max = 20
    max_capacity = 300
    max_start_window = 40.0
    early_coef_rand = (0.0, 0.2)
    late_coef_rand = (0.0, 1.0)
    if num_vehicles == 2:
        customer_demand_max = 10
    elif num_vehicles == 3:
        customer_demand_max = 15
    elif num_vehicles == 4:
        customer_demand_max = 20
    else:
        customer_demand_max = 25
    return (
        map_max,
        max_capacity,
        max_start_window,
        early_coef_rand,
        late_coef_rand,
        customer_demand_max,
    )


def get_150_customer_init_settings(
    num_vehicles: int,
) -> Tuple[chex.Array, chex.Array, chex.Array, chex.Array, chex.Array, chex.Array]:
    if not num_vehicles == 5:
        raise ValueError("num_vehicles must be 5 for num_customers=50")
    map_max = 20
    max_capacity = 180
    max_start_window = 60.0
    early_coef_rand = (0.1, 0.1)
    late_coef_rand = (0.5, 0.5)
    customer_demand_max = 10

    return (
        map_max,
        max_capacity,
        max_start_window,
        early_coef_rand,
        late_coef_rand,
        customer_demand_max,
    )


def get_init_settings(
    num_customers: int, num_vehicles: int
) -> Tuple[chex.Array, chex.Array, chex.Array, chex.Array, chex.Array, chex.Array]:
    if num_customers == 6:
        return get_6_customer_init_settings(num_vehicles)
    elif num_customers == 20:
        return get_20_customer_init_settings(num_vehicles)
    elif num_customers == 50:
        return get_50_customer_init_settings(num_vehicles)
    elif num_customers == 100:
        return get_100_customer_init_settings(num_vehicles)
    elif num_customers == 150:
        return get_150_customer_init_settings(num_vehicles)

    else:
        raise ValueError("num_customers must be one of [6, 20, 50, 100, 150]")
