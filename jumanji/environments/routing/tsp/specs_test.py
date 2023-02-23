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

from typing import Any

import chex
import jax.numpy as jnp
import pytest

from jumanji.environments.routing.tsp.env import TSP
from jumanji.environments.routing.tsp.types import Observation


class TestObservationSpec:
    env = TSP()
    observation_spec = env.observation_spec()
    observation = observation_spec.generate_value()

    def test_observation_spec__generate_value(self) -> None:
        """Test generating a value which conforms to the observation spec."""
        assert isinstance(self.observation, Observation)

    def test_observation_spec__validate(self) -> None:
        """Test the validation of an observation given the observation spec."""
        observation = self.observation_spec.validate(self.observation)
        # Check that a different shape breaks the validation
        with pytest.raises(ValueError):
            modified_shape_observation = observation._replace(  # type: ignore
                coordinates=observation.coordinates[None, ...]
            )
            self.observation_spec.validate(modified_shape_observation)
        # Check that a different dtype breaks the validation
        with pytest.raises(ValueError):
            modified_dtype_observation = observation._replace(  # type: ignore
                coordinates=observation.coordinates.astype(jnp.float16)
            )
            self.observation_spec.validate(modified_dtype_observation)
        # Check that validating another object breaks the validation
        with pytest.raises(Exception):
            self.observation_spec.validate(None)  # type: ignore

    @pytest.mark.parametrize(
        "arg_name, new_value",
        [
            (
                "coordinates_spec",
                observation_spec.coordinates_spec.replace(shape=(3, 4)),
            ),
            ("position_spec", observation_spec.position_spec.replace(name="new_name")),
            ("trajectory", observation_spec.trajectory.replace(name="new_name")),
            (
                "action_mask_spec",
                observation_spec.action_mask_spec.replace(shape=(3, 4)),
            ),
        ],
    )
    def test_observation_spec__replace(self, arg_name: str, new_value: Any) -> None:
        old_spec = self.observation_spec
        new_spec = old_spec.replace(**{arg_name: new_value})
        assert new_spec != old_spec
        assert getattr(new_spec, arg_name) == new_value
        for attr_name in {
            "coordinates_spec",
            "position_spec",
            "trajectory",
            "action_mask_spec",
        }.difference([arg_name]):
            chex.assert_equal(
                getattr(new_spec, attr_name), getattr(old_spec, attr_name)
            )
