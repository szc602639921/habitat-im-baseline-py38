#!/usr/bin/env python3

# Copyright (c) Facebook, Inc. and its affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from logging import Logger
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Optional,
    Sequence,
    Union,
    cast,
)

import numpy as np
import math
import magnum as mn
import random
from gym import spaces
from gym.spaces.box import Box
from numpy import ndarray

if TYPE_CHECKING:
    from torch import Tensor

import habitat_sim
from habitat.core.dataset import Episode
from habitat.core.registry import registry
from habitat.core.simulator import (
    AgentState,
    Config,
    DepthSensor,
    Observations,
    RGBSensor,
    SemanticSensor,
    Sensor,
    SensorSuite,
    ShortestPathPoint,
    Simulator,
    VisualObservation,
)
from habitat.core.spaces import Space
from habitat_sim.nav import NavMeshSettings
from habitat_sim.utils.common import quat_from_coeffs, quat_to_magnum
from habitat_sim.physics import MotionType

RGBSENSOR_DIMENSION = 3


def overwrite_config(config_from: Config, config_to: Any) -> None:
    r"""Takes Habitat Lab config and Habitat-Sim config structures. Overwrites
    Habitat-Sim config with Habitat Lab values, where a field name is present
    in lowercase. Mostly used to avoid :ref:`sim_cfg.field = hapi_cfg.FIELD`
    code.

    Args:
        config_from: Habitat Lab config node.
        config_to: Habitat-Sim config structure.
    """

    def if_config_to_lower(config):
        if isinstance(config, Config):
            return {key.lower(): val for key, val in config.items()}
        else:
            return config

    for attr, value in config_from.items():
        if hasattr(config_to, attr.lower()):
            setattr(config_to, attr.lower(), if_config_to_lower(value))


@registry.register_sensor
class HabitatSimRGBSensor(RGBSensor):
    sim_sensor_type: habitat_sim.SensorType

    def __init__(self, config: Config) -> None:
        self.sim_sensor_type = habitat_sim.SensorType.COLOR
        super().__init__(config=config)

    def _get_observation_space(self, *args: Any, **kwargs: Any) -> Box:
        return spaces.Box(
            low=0,
            high=255,
            shape=(self.config.HEIGHT, self.config.WIDTH, RGBSENSOR_DIMENSION),
            dtype=np.uint8,
        )

    def get_observation(
        self, sim_obs: Dict[str, Union[ndarray, bool, "Tensor"]]
    ) -> VisualObservation:
        obs = cast(Optional[VisualObservation], sim_obs.get(self.uuid, None))
        check_sim_obs(obs, self)

        # remove alpha channel
        obs = obs[:, :, :RGBSENSOR_DIMENSION]  # type: ignore[index]
        return obs


@registry.register_sensor
class HabitatSimDepthSensor(DepthSensor):
    sim_sensor_type: habitat_sim.SensorType
    min_depth_value: float
    max_depth_value: float

    def __init__(self, config: Config) -> None:
        self.sim_sensor_type = habitat_sim.SensorType.DEPTH

        if config.NORMALIZE_DEPTH:
            self.min_depth_value = 0
            self.max_depth_value = 1
        else:
            self.min_depth_value = config.MIN_DEPTH
            self.max_depth_value = config.MAX_DEPTH

        super().__init__(config=config)

    def _get_observation_space(self, *args: Any, **kwargs: Any) -> Box:
        return spaces.Box(
            low=self.min_depth_value,
            high=self.max_depth_value,
            shape=(self.config.HEIGHT, self.config.WIDTH, 1),
            dtype=np.float32,
        )

    def get_observation(
        self, sim_obs: Dict[str, Union[ndarray, bool, "Tensor"]]
    ) -> VisualObservation:
        obs = cast(Optional[VisualObservation], sim_obs.get(self.uuid, None))
        check_sim_obs(obs, self)
        if isinstance(obs, np.ndarray):
            obs = np.clip(obs, self.config.MIN_DEPTH, self.config.MAX_DEPTH)

            obs = np.expand_dims(
                obs, axis=2
            )  # make depth observation a 3D array
        else:
            obs = obs.clamp(self.config.MIN_DEPTH, self.config.MAX_DEPTH)  # type: ignore[attr-defined]

            obs = obs.unsqueeze(-1)  # type: ignore[attr-defined]

        if self.config.NORMALIZE_DEPTH:
            # normalize depth observation to [0, 1]
            obs = (obs - self.config.MIN_DEPTH) / (
                self.config.MAX_DEPTH - self.config.MIN_DEPTH
            )

        return obs


@registry.register_sensor
class HabitatSimSemanticSensor(SemanticSensor):
    sim_sensor_type: habitat_sim.SensorType

    def __init__(self, config):
        self.sim_sensor_type = habitat_sim.SensorType.SEMANTIC
        super().__init__(config=config)

    def _get_observation_space(self, *args: Any, **kwargs: Any):
        return spaces.Box(
            low=np.iinfo(np.uint32).min,
            high=np.iinfo(np.uint32).max,
            shape=(self.config.HEIGHT, self.config.WIDTH),
            dtype=np.uint32,
        )

    def get_observation(
        self, sim_obs: Dict[str, Union[ndarray, bool, "Tensor"]]
    ) -> VisualObservation:
        obs = cast(Optional[VisualObservation], sim_obs.get(self.uuid, None))
        check_sim_obs(obs, self)
        return obs


def check_sim_obs(obs: ndarray, sensor: Sensor) -> None:
    assert obs is not None, (
        "Observation corresponding to {} not present in "
        "simulator's observations".format(sensor.uuid)
    )


HabitatSimVizSensors = Union[
    HabitatSimRGBSensor, HabitatSimDepthSensor, HabitatSimSemanticSensor
]


@registry.register_simulator(name="Sim-v0")
class HabitatSim(habitat_sim.Simulator, Simulator):
    r"""Simulator wrapper over habitat-sim

    habitat-sim repo: https://github.com/facebookresearch/habitat-sim

    Args:
        config: configuration for initializing the simulator.
    """

    def __init__(self, config: Config) -> None:
        self.habitat_config = config
        agent_config = self._get_agent_config()

        sim_sensors = []
        for sensor_name in agent_config.SENSORS:
            sensor_cfg = getattr(self.habitat_config, sensor_name)
            sensor_type = registry.get_sensor(sensor_cfg.TYPE)

            assert sensor_type is not None, "invalid sensor type {}".format(
                sensor_cfg.TYPE
            )
            sim_sensors.append(sensor_type(sensor_cfg))

        self._sensor_suite = SensorSuite(sim_sensors)
        self.sim_config = self.create_sim_config(self._sensor_suite)
        self._current_scene = self.sim_config.sim_cfg.scene_id
        super().__init__(self.sim_config)
        self._action_space = spaces.Discrete(
            len(self.sim_config.agents[0].action_space)
        )
        self._prev_sim_obs: Optional[Observations] = None

        self.navmesh_settings = habitat_sim.NavMeshSettings()
        self.navmesh_settings.set_defaults()
        self.navmesh_settings.agent_radius = agent_config.RADIUS
        self.navmesh_settings.agent_height = agent_config.HEIGHT

    def create_sim_config(
        self, _sensor_suite: SensorSuite
    ) -> habitat_sim.Configuration:
        sim_config = habitat_sim.SimulatorConfiguration()
        # Check if Habitat-Sim is post Scene Config Update
        if not hasattr(sim_config, "scene_id"):
            raise RuntimeError(
                "Incompatible version of Habitat-Sim detected, please upgrade habitat_sim"
            )
        overwrite_config(
            config_from=self.habitat_config.HABITAT_SIM_V0,
            config_to=sim_config,
        )
        sim_config.scene_id = self.habitat_config.SCENE
        agent_config = habitat_sim.AgentConfiguration()
        overwrite_config(
            config_from=self._get_agent_config(), config_to=agent_config
        )

        sensor_specifications = []
        for sensor in _sensor_suite.sensors.values():
            sim_sensor_cfg = habitat_sim.SensorSpec()
            overwrite_config(
                config_from=sensor.config, config_to=sim_sensor_cfg
            )
            sim_sensor_cfg.uuid = sensor.uuid
            sim_sensor_cfg.resolution = list(
                sensor.observation_space.shape[:2]
            )
            sim_sensor_cfg.parameters["hfov"] = str(sensor.config.HFOV)

            # TODO(maksymets): Add configure method to Sensor API to avoid
            # accessing child attributes through parent interface
            # We know that the Sensor has to be one of these Sensors
            sensor = cast(HabitatSimVizSensors, sensor)
            sim_sensor_cfg.sensor_type = sensor.sim_sensor_type
            sim_sensor_cfg.gpu2gpu_transfer = (
                self.habitat_config.HABITAT_SIM_V0.GPU_GPU
            )
            sensor_specifications.append(sim_sensor_cfg)

        agent_config.sensor_specifications = sensor_specifications
        agent_config.action_space = registry.get_action_space_configuration(
            self.habitat_config.ACTION_SPACE_CONFIG
        )(self.habitat_config).get()

        return habitat_sim.Configuration(sim_config, [agent_config])

    @property
    def sensor_suite(self) -> SensorSuite:
        return self._sensor_suite

    @property
    def action_space(self) -> Space:
        return self._action_space

    def _update_agents_state(self) -> bool:
        is_updated = False
        for agent_id, _ in enumerate(self.habitat_config.AGENTS):
            agent_cfg = self._get_agent_config(agent_id)
            if agent_cfg.IS_SET_START_STATE:
                self.set_agent_state(
                    agent_cfg.START_POSITION,
                    agent_cfg.START_ROTATION,
                    agent_id,
                )
                is_updated = True

        return is_updated

    def reset(self) -> Observations:
        sim_obs = super().reset()
        if self._update_agents_state():
            sim_obs = self.get_sensor_observations()

        self._prev_sim_obs = sim_obs
        return self._sensor_suite.get_observations(sim_obs)

    def step(self, action: Union[str, int]) -> Observations:
        sim_obs = super().step(action)
        self._prev_sim_obs = sim_obs
        observations = self._sensor_suite.get_observations(sim_obs)
        return observations

    def render(self, mode: str = "rgb") -> Any:
        r"""
        Args:
            mode: sensor whose observation is used for returning the frame,
                eg: "rgb", "depth", "semantic"

        Returns:
            rendered frame according to the mode
        """
        sim_obs = self.get_sensor_observations()
        observations = self._sensor_suite.get_observations(sim_obs)

        output = observations.get(mode)
        assert output is not None, "mode {} sensor is not active".format(mode)
        if not isinstance(output, np.ndarray):
            # If it is not a numpy array, it is a torch tensor
            # The function expects the result to be a numpy array
            output = output.to("cpu").numpy()

        return output

    def reconfigure(self, habitat_config: Config) -> None:
        # TODO(maksymets): Switch to Habitat-Sim more efficient caching
        is_same_scene = habitat_config.SCENE == self._current_scene
        self.habitat_config = habitat_config
        self.sim_config = self.create_sim_config(self._sensor_suite)
        if not is_same_scene:
            self._current_scene = habitat_config.SCENE
            self.close()
            super().reconfigure(self.sim_config)

        self._update_agents_state()

        self.sim_object_to_objid_mapping = {}
        self.objid_to_sim_object_mapping = {}
        self.obj_sem_id_to_sem_category_mapping = {}

        self._initialize_THDA_scene()

    def remove_all_objects(self):
        for id in self.get_existing_object_ids():
            self.remove_object(id)

    def get_rotation_vec(self, object_id):
        quat = self.get_rotation(object_id)
        return np.array(quat.vector).tolist() + [quat.scalar]

    # THDA Initialization
    def _initialize_THDA_scene(self):
        print("DEBUG!!!!!!!!!!!!!!!!!!: THDA Initialization")
        self.remove_all_objects()
        self.recompute_navmesh(self.pathfinder, self.navmesh_settings, True)
        # Retrieving scene state from config
        if (
            not hasattr(self.habitat_config, "scene_state")
            or self.habitat_config.scene_state is None
        ):
            return
        objects = self.habitat_config.scene_state[0]["objects"]
        print("DEBUG!!!!!!!!!!!!!!!!!!: THDA Initialization")
        obj_templates_mgr = self.get_object_template_manager()

        # self.remove_all_objects()
        # self.recompute_navmesh(self.pathfinder, self.navmesh_settings, True)

        self.sim_object_to_objid_mapping = {}
        self.objid_to_sim_object_mapping = {}
        self.obj_sem_id_to_sem_category_mapping = {}

        if objects is not None:
            for object in objects:
                object_template = f"{object.object_template}"
                print(f"DEBUG!!!!!!!!!!!!!!!!!!:{object_template}")
                object_pos = object.position
                object_rot = object.rotation
                object_template_handles = (
                    obj_templates_mgr.get_file_template_handles(
                        object_template
                    )
                )

                if len(object_template_handles) >= 1:
                    object_template_handle = object_template_handles[0]
                    # logger.info(f"Reusing template handle.{object_template_handle} | {obj_templates_mgr.get_num_templates()}")
                else:
                    # if obj_templates_mgr.get_num_templates() < 20:
                    # logger.info(f"Creating new template handle. {object_template} | {obj_templates_mgr.get_num_templates()}")
                    _object_template_ids = (
                        obj_templates_mgr.load_object_configs(object_template)
                    )
                    assert (
                        len(_object_template_ids) > 0
                    ), f"Object template '{object_template}' wasn't found."
                    object_template_id = _object_template_ids[0]
                    object_attr = obj_templates_mgr.get_template_by_ID(
                        object_template_id
                    )
                    if object.scale:
                        object_attr.scale = mn.Vector3(object.scale)
                        # print(f"object.scale: {object.scale}")
                    else:
                        object_attr.scale = mn.Vector3(5)
                    obj_templates_mgr.register_template(object_attr)

                    # logger.error(f"SCENE: {self.habitat_config.SCENE}")
                    object_template_handle = object_attr.handle
                    # else:
                    # object_template_handle = obj_templates_mgr.get_random_template_handle()
                    # logger.info(f"Random object {object_template_handle} instead of {object_template}")

                object_id = self.add_object_by_handle(object_template_handle)

                self.sim_object_to_objid_mapping[object_id] = object.object_id
                self.objid_to_sim_object_mapping[object.object_id] = object_id

                # Return back
                self.set_object_semantic_id(int(object.object_id), object_id)
                # [self.get_object_scene_node(id).semantic_id for id in self.get_existing_object_ids()]
                self.obj_sem_id_to_sem_category_mapping[
                    int(object.object_id)
                ] = object.semantic_category_id

                if object_pos is not None:
                    self.set_translation(object_pos, object_id)
                    if isinstance(object_rot, list):
                        object_rot = quat_from_coeffs(object_rot)
                    if object_rot is not None:
                        object_rot = quat_to_magnum(object_rot)
                        self.set_rotation(object_rot, object_id)
                else:
                    self.sample_object_state(object_id)
                    self.recompute_navmesh(
                        self.pathfinder, self.navmesh_settings, True
                    )

                # self.sample_object_state(object_id)

                self.set_object_motion_type(MotionType.STATIC, object_id)
                # print(f"rotation: {self.get_rotation_vec(object_id)}")
                # print(f"position: {np.array(self.get_translation(object_id)).tolist()}")
                # print(f"{self.sample_navigable_point()}")

            # Recompute the navmesh after placing all the objects.
            self.recompute_navmesh(
                self.pathfinder, self.navmesh_settings, True
            )
            # logger.info("Inserting objects and recompute navmesh")
    
    def sample_object_state(
        sim,
        object_id,
        from_navmesh=True,
        maintain_object_up=True,
        max_tries=100,
        bb=None,
    ):
        # check that the object is not STATIC
        if (
            sim.get_object_motion_type(object_id)
            is habitat_sim.physics.MotionType.STATIC
        ):
            print("sample_object_state : Object is STATIC, aborting.")
        if from_navmesh:
            if not sim.pathfinder.is_loaded:
                print("sample_object_state : No pathfinder, aborting.")
                return False
        elif not bb:
            print(
                "sample_object_state : from_navmesh not specified and no bounding box provided, aborting."
            )
            return False
        tries = 0
        valid_placement = False
        # Note: following assumes sim was not reconfigured without close
        scene_collision_margin = 0.04
        while not valid_placement and tries < max_tries:
            tries += 1
            # initialize sample location to random point in scene bounding box
            sample_location = np.array([0, 0, 0])
            if from_navmesh:
                # query random navigable point
                sample_location = sim.pathfinder.get_random_navigable_point()
            else:
                sample_location = np.random.uniform(bb.min, bb.max)
            # set the test state
            # to debug sim.set_translation(sim.pathfinder.get_random_navigable_point(), object_id), sim.get_translation(object_id)
            sim.set_translation(sample_location, object_id)
            if maintain_object_up:
                # random rotation only on the Y axis
                y_rotation = mn.Quaternion.rotation(
                    mn.Rad(random.random() * 2 * math.pi),
                    mn.Vector3(0, 1.0, 0),
                )
                sim.set_rotation(
                    y_rotation * sim.get_rotation(object_id), object_id
                )
            else:
                # unconstrained random rotation
                sim.set_rotation(ut.random_quaternion(), object_id)

            # raise object such that lowest bounding box corner is above the navmesh sample point.
            if from_navmesh:
                obj_node = sim.get_object_scene_node(object_id)
                xform_bb = habitat_sim.geo.get_transformed_bb(
                    obj_node.cumulative_bb, obj_node.transformation
                )
                # also account for collision margin of the scene
                y_translation = mn.Vector3(
                    0, xform_bb.size_y() / 2.0 + scene_collision_margin, 0
                )
                sim.set_translation(
                    y_translation + sim.get_translation(object_id), object_id
                )

            # test for penetration with the environment
            if not sim.contact_test(object_id):
                valid_placement = True

        if not valid_placement:
            return False
        return True

    def geodesic_distance(
        self,
        position_a: Union[Sequence[float], ndarray],
        position_b: Union[Sequence[float], Sequence[Sequence[float]]],
        episode: Optional[Episode] = None,
    ) -> float:
        if episode is None or episode._shortest_path_cache is None:
            path = habitat_sim.MultiGoalShortestPath()
            if isinstance(position_b[0], (Sequence, np.ndarray)):
                path.requested_ends = np.array(position_b, dtype=np.float32)
            else:
                path.requested_ends = np.array(
                    [np.array(position_b, dtype=np.float32)]
                )
        else:
            path = episode._shortest_path_cache

        path.requested_start = np.array(position_a, dtype=np.float32)

        self.pathfinder.find_path(path)

        if episode is not None:
            episode._shortest_path_cache = path

        return path.geodesic_distance

    def action_space_shortest_path(
        self,
        source: AgentState,
        targets: Sequence[AgentState],
        agent_id: int = 0,
    ) -> List[ShortestPathPoint]:
        r"""
        Returns:
            List of agent states and actions along the shortest path from
            source to the nearest target (both included). If one of the
            target(s) is identical to the source, a list containing only
            one node with the identical agent state is returned. Returns
            an empty list in case none of the targets are reachable from
            the source. For the last item in the returned list the action
            will be None.
        """
        raise NotImplementedError(
            "This function is no longer implemented. Please use the greedy "
            "follower instead"
        )

    @property
    def up_vector(self) -> np.ndarray:
        return np.array([0.0, 1.0, 0.0])

    @property
    def forward_vector(self) -> np.ndarray:
        return -np.array([0.0, 0.0, 1.0])

    def get_straight_shortest_path_points(self, position_a, position_b):
        path = habitat_sim.ShortestPath()
        path.requested_start = position_a
        path.requested_end = position_b
        self.pathfinder.find_path(path)
        return path.points

    def sample_navigable_point(self) -> List[float]:
        return self.pathfinder.get_random_navigable_point().tolist()

    def is_navigable(self, point: List[float]) -> bool:
        return self.pathfinder.is_navigable(point)

    def semantic_annotations(self):
        r"""
        Returns:
            SemanticScene which is a three level hierarchy of semantic
            annotations for the current scene. Specifically this method
            returns a SemanticScene which contains a list of SemanticLevel's
            where each SemanticLevel contains a list of SemanticRegion's where
            each SemanticRegion contains a list of SemanticObject's.

            SemanticScene has attributes: aabb(axis-aligned bounding box) which
            has attributes aabb.center and aabb.sizes which are 3d vectors,
            categories, levels, objects, regions.

            SemanticLevel has attributes: id, aabb, objects and regions.

            SemanticRegion has attributes: id, level, aabb, category (to get
            name of category use category.name()) and objects.

            SemanticObject has attributes: id, region, aabb, obb (oriented
            bounding box) and category.

            SemanticScene contains List[SemanticLevels]
            SemanticLevel contains List[SemanticRegion]
            SemanticRegion contains List[SemanticObject]

            Example to loop through in a hierarchical fashion:
            for level in semantic_scene.levels:
                for region in level.regions:
                    for obj in region.objects:
        """
        return self.semantic_scene

    def _get_agent_config(self, agent_id: Optional[int] = None) -> Any:
        if agent_id is None:
            agent_id = self.habitat_config.DEFAULT_AGENT_ID
        agent_name = self.habitat_config.AGENTS[agent_id]
        agent_config = getattr(self.habitat_config, agent_name)
        return agent_config

    def get_agent_state(self, agent_id: int = 0) -> habitat_sim.AgentState:
        assert agent_id == 0, "No support of multi agent in {} yet.".format(
            self.__class__.__name__
        )
        return self.get_agent(agent_id).get_state()

    def set_agent_state(
        self,
        position: List[float],
        rotation: List[float],
        agent_id: int = 0,
        reset_sensors: bool = True,
    ) -> bool:
        r"""Sets agent state similar to initialize_agent, but without agents
        creation. On failure to place the agent in the proper position, it is
        moved back to its previous pose.

        Args:
            position: list containing 3 entries for (x, y, z).
            rotation: list with 4 entries for (x, y, z, w) elements of unit
                quaternion (versor) representing agent 3D orientation,
                (https://en.wikipedia.org/wiki/Versor)
            agent_id: int identification of agent from multiagent setup.
            reset_sensors: bool for if sensor changes (e.g. tilt) should be
                reset).

        Returns:
            True if the set was successful else moves the agent back to its
            original pose and returns false.
        """
        agent = self.get_agent(agent_id)
        new_state = self.get_agent_state(agent_id)
        new_state.position = position
        new_state.rotation = rotation

        # NB: The agent state also contains the sensor states in _absolute_
        # coordinates. In order to set the agent's body to a specific
        # location and have the sensors follow, we must not provide any
        # state for the sensors. This will cause them to follow the agent's
        # body
        new_state.sensor_states = {}
        agent.set_state(new_state, reset_sensors)
        return True

    def get_observations_at(
        self,
        position: Optional[List[float]] = None,
        rotation: Optional[List[float]] = None,
        keep_agent_at_new_pose: bool = False,
    ) -> Optional[Observations]:
        current_state = self.get_agent_state()
        if position is None or rotation is None:
            success = True
        else:
            success = self.set_agent_state(
                position, rotation, reset_sensors=False
            )

        if success:
            sim_obs = self.get_sensor_observations()

            self._prev_sim_obs = sim_obs

            observations = self._sensor_suite.get_observations(sim_obs)
            if not keep_agent_at_new_pose:
                self.set_agent_state(
                    current_state.position,
                    current_state.rotation,
                    reset_sensors=False,
                )
            return observations
        else:
            return None

    def distance_to_closest_obstacle(
        self, position: ndarray, max_search_radius: float = 2.0
    ) -> float:
        return self.pathfinder.distance_to_closest_obstacle(
            position, max_search_radius
        )

    def island_radius(self, position: Sequence[float]) -> float:
        return self.pathfinder.island_radius(position)

    @property
    def previous_step_collided(self):
        r"""Whether or not the previous step resulted in a collision

        Returns:
            bool: True if the previous step resulted in a collision, false otherwise

        Warning:
            This feild is only updated when :meth:`step`, :meth:`reset`, or :meth:`get_observations_at` are
            called.  It does not update when the agent is moved to a new loction.  Furthermore, it
            will _always_ be false after :meth:`reset` or :meth:`get_observations_at` as neither of those
            result in an action (step) being taken.
        """
        return self._prev_sim_obs.get("collided", False)
