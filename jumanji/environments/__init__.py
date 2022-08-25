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

from jumanji.environments.combinatorial import binpack, knapsack, routing, tsp
from jumanji.environments.combinatorial.binpack.env import BinPack
from jumanji.environments.combinatorial.knapsack.env import Knapsack
from jumanji.environments.combinatorial.routing.env import Routing
from jumanji.environments.combinatorial.tsp.env import TSP
from jumanji.environments.env import Environment, State
from jumanji.environments.games import connect4, snake
from jumanji.environments.games.connect4.env import Connect4
from jumanji.environments.games.snake.env import Snake