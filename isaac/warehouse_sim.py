import argparse
import base64
import collections
import io
import json
import math
import queue
import threading
import time
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--api", default="http://localhost:8010")
parser.add_argument("--port", type=int, default=8020)
parser.add_argument("--width", type=int, default=1280)
parser.add_argument("--height", type=int, default=720)
parser.add_argument("--collect", type=int, default=0, help="record N scripted grasp demonstrations and exit")
parser.add_argument("--collect-push", type=int, default=0, help="record N paddle push demonstrations (deep shelving) and exit")
parser.add_argument("--push-policy", choices=["expert", "vla"], default="expert", help="with --collect-push: vla evaluates the push policy at --policy-url instead of recording expert demos")
parser.add_argument("--push-eval", action="store_true", help="with --collect-push: log success per trial instead of saving demos")
parser.add_argument("--teleop", action="store_true", help="keyboard push demonstrations in the browser on --port, saved to --out")
parser.add_argument("--push-blend", type=int, default=5, help="blend this many leading actions of each new push chunk with the previous chunk")
parser.add_argument("--push-lead", type=int, default=1, help="command the push policy action this many steps ahead to offset tracking lag")
parser.add_argument("--push-settle", type=int, default=0, help="max ticks to wait for the arm to reach each push action")
parser.add_argument("--push-execute", type=int, default=5, help="push policy actions executed per query")
parser.add_argument("--marker-state", action="store_true", help="append the head-image marker center to the push policy state")
parser.add_argument("--push-speed", type=float, default=0.0, help="override the sideways push speed in m/s for every blocker")
parser.add_argument("--out", default="/workspace/vla/data/raw")
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--policy-url", default="http://localhost:8030")
parser.add_argument("--livestream", action="store_true", help="serve the viewport to the Isaac Sim WebRTC Streaming Client")
parser.add_argument("--watch", action="store_true", help="serve the head camera as MJPEG on --port during eval or collection")
parser.add_argument("--graphics", choices=("basic", "high"), default="basic")
parser.add_argument("--eval-grasp", type=int, default=0, help="compare grasp policies on N randomized trials")
parser.add_argument("--policies", nargs="+", default=["scripted", "vla"])
parser.add_argument("--grasp", choices=("kinematic", "physical"), default="kinematic",
                    help="kinematic attaches the item on close; physical lifts it through finger contact")
parser.add_argument("--scenario", choices=("nominal", "hard"), default="hard",
                    help="hard adds toppled pickables, a cluttered receiving tote and mixed-SKU deep shelving")
parser.add_argument("--eval-scene", choices=("nominal", "toppled", "tote", "deep", "hard"), default="hard")
parser.add_argument("--perturb-rate", type=float, default=0.0,
                    help="share of demo episodes with an injected perturbation (nudge, offset, regrasp) and recorded recovery")
parser.add_argument("--pose", choices=("depth", "sim"), default="depth",
                    help="depth estimates the target pose from the head depth camera; sim reads the simulator (privileged expert)")
args, _ = parser.parse_known_args()

from isaacsim import SimulationApp

app = SimulationApp({"headless": True, "width": args.width, "height": args.height})
if args.livestream:
    from isaacsim.core.utils.extensions import enable_extension

    app.set_setting("/app/window/drawMouse", True)
    enable_extension("omni.kit.livestream.webrtc")
app.set_setting("/physics/updateToUsd", True)
app.set_setting("/physics/outputVelocitiesLocalSpace", False)
if args.graphics == "high":
    for key, value in {
        "/rtx/rendermode": "RaytracedLighting",
        "/rtx/post/aa/op": 3,
        "/rtx/ambientOcclusion/enabled": True,
        "/rtx/reflections/enabled": True,
        "/rtx/indirectDiffuse/enabled": True,
        "/rtx/shadows/enabled": True,
        "/rtx/post/tonemap/op": 6,
        "/rtx/post/histogram/enabled": True,
    }.items():
        app.set_setting(key, value)

import numpy as np
import omni.replicator.core as rep
from isaacsim.core.api import World
from isaacsim.core.api.objects import FixedCuboid, VisualCuboid
from isaacsim.core.prims import SingleArticulation, SingleXFormPrim
from isaacsim.core.utils.rotations import euler_angles_to_quat, quat_to_rot_matrix, rot_matrix_to_quat
from isaacsim.core.utils.stage import add_reference_to_stage, get_current_stage
from isaacsim.core.utils.types import ArticulationAction
from isaacsim.core.utils.viewports import set_camera_view
from isaacsim.robot_motion.motion_generation import (
    ArticulationKinematicsSolver,
    LulaKinematicsSolver,
    interface_config_loader,
)
from isaacsim.storage.native import get_assets_root_path
from PIL import Image
from pxr import Gf, PhysxSchema, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade

PHYSICS_DT = 1 / 60
MOTION_SPEEDUP = 2.5
BASE_SPEED = 1.2
BASE_TURN_SPEED = 1.8
BASE_ACCEL = 0.6
BASE_TURN_ACCEL = 1.0
YCB_ROOT = "/Isaac/Props/YCB/Axis_Aligned"
YCB_SIZES = {
    "002_master_chef_can": (0.102, 0.102, 0.140), "003_cracker_box": (0.164, 0.072, 0.213),
    "004_sugar_box": (0.093, 0.045, 0.176), "005_tomato_soup_can": (0.068, 0.068, 0.102),
    "006_mustard_bottle": (0.096, 0.058, 0.191), "008_pudding_box": (0.114, 0.038, 0.090),
    "009_gelatin_box": (0.089, 0.030, 0.073), "010_potted_meat_can": (0.102, 0.058, 0.084),
    "021_bleach_cleanser": (0.102, 0.068, 0.251), "025_mug": (0.117, 0.093, 0.081),
    "036_wood_block": (0.090, 0.091, 0.206), "061_foam_brick": (0.078, 0.053, 0.051),
}
BIN_FRONT_BAND = 0.14
BIN_MARGIN = 0.05
ITEM_GAP = 0.025
CATALOG_YAW = 0.35
CLUSTER_GAP = 0.02
CLUSTER_JITTER = 0.01
CLUSTER_YAW = 0.12
TOPPLE_RATE = 0.12
TOPPLE_MAX_CLUSTER = 6
STAGING_HEIGHT = 0.3
STAGING_STANDOFF = 0.75
SURFACE_LIFT = 0.003
SHELF_LIGHT_INTENSITY = 40000.0
GRIP_FRICTION = 1.2
BACKGROUND_MASS = 0.3
GRASP_STANDOFF = 0.16
GRASP_DEPTH = 0.0
GRASP_PITCH = 0.0
SHORT_GRASP_PITCH = 0.2
GRASP_BELOW_TOP = 0.035
GRASP_PITCH_FALLBACKS = (0.0, 0.2, 0.4, 0.6)
SHORT_ITEM = 0.15
LINEAR_STEP = 0.01
LINEAR_MAX_JOINT_STEP = 0.25
CLEARANCE_X = 0.05
BACK_OFF = 0.08
LIFT_HEIGHT = 0.08
RETRACT = 0.2
HOLD_STEPS = 30
HOLD_TOLERANCE = 0.08
CLOSE_STEPS = 24
FINGER_STIFFNESS = 2000.0
FINGER_DAMPING = 100.0
FINGER_MAX_FORCE = 40.0
ITEM_SOLVER_ITERATIONS = 16
SHELF_DEPTH = 0.6
SHELF_BOARD = 0.04
BIN_LENGTH = 1.3
APPROACH_OFFSET = 1.0
IK_ATTEMPTS = 8
IK_NEAR = 1.0
NATURAL_REACH = np.array([0.0, 0.3, 0.0, -2.0, 0.0, 2.3, 0.785])
IK_REACH_SEEDS = [np.array([pan, shoulder, 0.0, elbow, 0.0, wrist, twist])
                  for pan in (0.0, -0.6, 0.6, -math.pi / 2, math.pi / 2, -2.5, 2.5)
                  for shoulder, elbow, wrist in ((0.2, -2.0, 2.2), (0.6, -1.6, 2.2), (-0.3, -2.4, 2.4))
                  for twist in (0.785, 0.785 - math.pi / 2, 0.785 + math.pi / 2)]
SCAN_STANDOFF = 0.8
CORRIDOR_Y = -0.6
TOTE_OFFSET = np.array([0.0, -0.37, 0.60])
DECK_HEIGHT = 0.28
DECK_SLOTS = [np.array([dx, dy]) for dx in (0.07, -0.07) for dy in (-0.09, 0.0, 0.09)]
BASE_HALF_WIDTH = 0.40
ARM_MOUNT = 0.30
WORK_HEADING = 0.0
WORK_REACH = 0.50
DISPATCH_REACH = 0.20
STANCE_CLEARANCE = {"storage": 0.10, "staging": 0.10, "dispatch": 0.05}
PICK_GAP = 0.06
CONVEYOR_FACE_X = 11.87
UNDOCK = 0.35
CARRY_CLEARANCE = 0.12
HOVER_LIFT = np.array([0.0, 0.0, 0.18])
TOTE_HOVER_LIFTS = (0.18, 0.08, 0.0)
SET_DOWN_TURNS = (0.0, math.pi / 2, -math.pi / 2, math.pi, 0.5, -0.5)
OUTBOUND_CLEARANCE = 0.12
FRONT_GRASP = euler_angles_to_quat(np.array([0.0, math.pi / 2, 0.0]))
STREAM_EVERY = 3
INSPECT_RES = (896, 384)
INSPECT_APERTURE = 20.955
WAREHOUSE_PROPS = "/Isaac/Environments/Simple_Warehouse/Props"
RACK_SHELF_SIZE = np.array([4.0, 1.076, 0.37])
RACK_FRAME_SIZE = np.array([0.126, 1.0, 3.0])
PALLET_SIZE = np.array([1.213, 1.003, 0.211])
FLOOR_TILE = 6.0
DECOR_LEVELS = (0.15, 1.15, 2.15)
DECOR_BAY = 4.0
DECOR_CARTONS = {"SM_CardBoxA_01": (0.7, 0.5), "SM_CardBoxB_01": (0.5, 0.5), "SM_CardBoxC_01": (0.5, 0.5),
                 "SM_CardBoxD_01": (0.38, 0.25)}
CONVEYOR_X = 12.45
CONVEYOR_START_Y = 0.6
CONVEYOR_SEGMENT = 2.719
CONVEYOR_SEGMENTS = 2
CONVEYOR_WIDTH = 1.15
CURVE_PIVOT_OFFSET = 1.566
CURVE_CENTERLINE = 1.53
CONVEYOR_HIDDEN_PARTS = ("Rubberbands", "SM_ConveyorBelt_A08_Rubberband", "M_ConveyorBelt_A01_Cables",
                         "M_ConveyorBelt_A01_WirePipes", "M_ConveyorBelt_A01_Pipes")
CONVEYOR_HEIGHT_SCALE = 0.8
ROLLER_TOP = 0.769 * CONVEYOR_HEIGHT_SCALE
TOTE_X = 12.16
TOTE_SCALE = np.array([1.8, 1.8, 0.6])
TOTE_SIZE = np.array([0.198, 0.296, 0.146]) * TOTE_SCALE
TOTE_SPEED = 0.6
DISPATCH_STANDOFF = 0.6
DISPATCH_BACKOFF = 0.6
OUTBOUND_SLOTS = 10
SAFE_CARRY_Z = 1.08
CARRY_HEIGHTS = (SAFE_CARRY_Z, 1.03, 0.98)
DISPATCH_RETREAT = 0.25
TOTE_LIFT_HEIGHTS = (1.05, 1.0, 0.95, 0.9)
TOTE_AUDIT_FOCAL = 22.0
RELEASE_POSES = [euler_angles_to_quat(np.array([0.0, pitch, 0.0])) for pitch in (math.pi * 0.95, math.pi * 0.75, math.pi / 2)]
POLICY_RES = (256, 256)
HEAD_FOCAL = 13.0
CONTROL_EVERY = 6
FINGERTIP_OFFSET = np.array([0.0, 0.0, 0.1034])
TOOLBOX_OFFSET = np.array([0.42, -0.30])
TOOLBOX_TOP = 0.62
MOUNT_METAL = np.array([0.25, 0.25, 0.28])
TOTE_TOP = TOTE_OFFSET[2] - 0.08
TOTE_STAND_OFFSET = np.array([0.0, -0.30])
ROBOT_TOTE_SCALE = np.array([1.6, 1.1, 0.55])
ROBOT_TOTE_SIZE = np.array([0.198, 0.296, 0.146]) * ROBOT_TOTE_SCALE
ROBOT_TOTE_WALL = 0.03
HAND_RIM_CLEARANCE = 0.04
HARDWARE = [
    (tuple(TOTE_STAND_OFFSET), DECK_HEIGHT + 0.003, (0.10, 0.10, 0.006), MOUNT_METAL),
    (tuple(TOTE_OFFSET[:2]), TOTE_TOP - 0.008, (0.24, 0.24, 0.016), MOUNT_METAL),
]
SHOW_TOOLS = False
TOOL_SLOTS = {"paddle": np.array([0.0, 0.0])}
TOOL_HOVER = 0.08
TOOL_SPEED = 0.08
TOOL_PARTS = {
    "paddle": [((0.0, 0.0, -0.005), (0.03, 0.02, 0.07), False), ((0.0, 0.0, 0.075), (0.12, 0.008, 0.09), True)],
}
TOOL_TIPS = {"paddle": np.array([0.0, 0.0, 0.08])}
TOOL_STOW_HEIGHT = {"paddle": 0.13}
TOOL_GRIP = {"paddle": 0.01}
TOOL_MOUNTS = {"paddle": np.eye(3)}
TOOL_COLORS = {"paddle": (0.95, 0.55, 0.1)}
GRASP_TOLERANCE = 0.05
VLA_MAX_STEPS = 120
RESHUFFLE_EVERY = 10
VLA_EXECUTE_ACTIONS = 5
VLA_PUSH_QUERIES = 45
VLA_PUSH_HOME_TOLERANCE = 0.04
TELEOP_SPEED = 0.08
TELEOP_FINE_SPEED = 0.02
TELEOP_TURN = 0.8
TELEOP_FINE_TURN = 0.2
TELEOP_KEY_TIMEOUT = 0.4
TELEOP_MAX_JOINT_STEP = 0.01
TELEOP_IK_JUMP = 0.15
TELEOP_FLOOR_MARGIN = 0.005
TELEOP_VIEW_SIZE = (480, 360)
TELEOP_VIEW_FOCAL = 14.0
TELEOP_VIEW_POSES = {"top": ((0.62, 0.0, 1.45), (0.95, 0.0, 0.35)), "side": ((0.95, 0.85, 0.5), (0.95, -0.1, 0.4))}
TELEOP_DIRECTIONS = {"w": np.array([1.0, 0.0, 0.0]), "s": np.array([-1.0, 0.0, 0.0]), "a": np.array([0.0, 1.0, 0.0]),
                     "d": np.array([0.0, -1.0, 0.0]), "r": np.array([0.0, 0.0, 1.0]), "f": np.array([0.0, 0.0, -1.0])}
TELEOP_MOTION_KEYS = set(TELEOP_DIRECTIONS) | {"q", "e"}
VLA_PUSH_SETTLE_TOLERANCE = 0.005
PERCEPTION_RES = (512, 512)
HEAD_HEIGHT = 1.3
HEAD_REACH = 0.8
HEAD_LOOK_ABOVE = 0.08
UPPER_LEVEL_Z = 0.6
UPPER_HEAD_REACH = 0.6
UPPER_HEAD_HEIGHT = 1.6
COMFORT_REACH = 0.5
REPOSITION_LIMIT = 0.3
MIN_VISIBLE_POINTS = 40
MAX_GRASP_WIDTH = 0.075
ATTACH_MARGIN = 0.015
PICK_TOPPLE_RATE = 0.25
TOPPLE_BIN = "B-01-1"
DEEP_BIN = "C-04-1"
TOTE_BIN = "STAGING-1"
FINGER_REACH = 0.05
DEEP_PAIR_SLOTS = (-0.28, 0.28)
DEEP_EXTRA_SLOTS = (0.50, -0.50)
DEEP_ROW_GAP = 0.12
DEEP_JITTER = 0.015
DEEP_BLOCKER_YAW = math.radians(45)
GRASP_READY_LIFT = 0.12
RECEIVING_TOTE_SCALE = np.array([2.5, 2.5, 0.8])
RECEIVING_TOTE_SIZE = np.array([0.198, 0.296, 0.146]) * RECEIVING_TOTE_SCALE
RECEIVING_TOTE_INSET = 0.03
TOTE_WALL = 0.01
TOTE_CLUTTER = ("009_gelatin_box", "061_foam_brick", "008_pudding_box", "010_potted_meat_can", "004_sugar_box",
                "025_mug", "009_gelatin_box", "061_foam_brick")
TOTE_LAYER = 4
PALLET_TOP = 0.211
DECOR_TOTE_SCALE = np.array([2.6, 1.6, 1.0])
DECOR_TOTE_SIZE = np.array([0.198, 0.296, 0.146]) * DECOR_TOTE_SCALE
DECOR_PALLETS = ((12.35, 9.45), (13.75, 8.2))
DECOR_FILLED_TOTES = ((12.06, 9.45, PALLET_TOP), (12.64, 9.45, PALLET_TOP), (13.46, 8.2, PALLET_TOP))
DECOR_EMPTY_STACKS = ((14.04, 8.2, PALLET_TOP, 2), (13.75, 9.45, 0.0, 3))
DECOR_TOTE_ITEMS = 7
YAW_SPREAD_PICKABLE = 1.0
YAW_SPREAD_OTHER = 0.5
SETTLE_STEPS = 120
TOP_DOWN_LIFT = 0.15
SHELF_TOP_DOWN_LIFT = 0.05
RACK_RETREAT = 0.35
PUSH_CLEARANCE = 0.04
PUSH_HEIGHT = 0.075
PUSH_STANDOFF = 0.08
PUSH_ENTRY = 0.0
PUSH_BACKOFF = 0.0
PUSH_READY = 0.0
PUSH_READY_LIFT = 0.15
PUSH_INSERT_SPEED = 0.06
PUSH_SPEED = 0.03
PUSH_GOAL_CLEARANCE = 0.07
PUSH_OVERSHOOT = 0.02
PUSH_SLIP_MARGIN = 0.04
PUSH_MAX_CARRY = 0.12
PUSH_STROKE_SPEED = 0.08
UPRIGHT_COS = 0.9
PUSH_STEP = 0.02
PUSH_PLAN_STEP = 0.01
PUSH_MAX_JOINT_STEP = 0.15
RELAXED_JOINT_STEP = 0.6
ATTACH_ON_CLOSE = 0.10
PUSH_TRACK_TOLERANCE = 0.03
WIDTH_WEIGHT = 20.0
STEEP_WEIGHT = 0.8
APPROACH_BACKOFF = 0.0
WRIST_FOCAL = 10.0
WRIST_OFFSET = np.array([-0.07, 0.0, 0.02])
WRIST_LOOK = 0.06
MARKER_NOISE = 0.01
MARKER_RADIUS = 6
MARKER_COLOR = np.array([255, 0, 255], dtype=np.uint8)
NUDGE_RANGE = (0.015, 0.03)
NUDGE_TICKS = 12
OFFSET_RANGE = (0.02, 0.035)
STANCE_JITTER = (0.06, 0.2, 0.04)
DEMO_SCENES = ("toppled", "tote", "deep", "shelf")



def api_get(path: str):
    with urllib.request.urlopen(args.api + path, timeout=10) as response:
        return json.loads(response.read())


class SimError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Item:
    def __init__(self, prim: SingleXFormPrim, sku: str | None, ycb: str):
        self.prim = prim
        self.sku = sku
        self.ycb = ycb
        self.size = np.array(YCB_SIZES[ycb])
        self.location_id: str | None = None
        self.position = np.zeros(3)
        self.yaw = 0.0
        self.toppled = False
        self.flipped = False
        self.deck_slot = 0
        self.pinned = False

    @property
    def path(self) -> str:
        return self.prim.prim_path

    @property
    def footprint(self) -> np.ndarray:
        return np.array([self.size[2], self.size[0], self.size[1]]) if self.toppled else self.size

    @property
    def radius(self) -> float:
        return float(np.linalg.norm(self.footprint[:2])) / 2

    def move(self, position, yaw: float | None = None) -> None:
        self.position = np.asarray(position, dtype=float)
        if yaw is not None:
            self.yaw = float(yaw)
        if self.toppled:
            euler = [0.0, 0.0, self.yaw - math.pi / 2]
        else:
            euler = [math.pi / 2 if self.flipped else -math.pi / 2, 0.0, self.yaw]
        self.prim.set_world_pose(self.position, euler_angles_to_quat(np.array(euler)))

    def rest_on(self, surface: np.ndarray, yaw: float | None = None) -> None:
        self.move(surface + np.array([0.0, 0.0, self.footprint[2] / 2 + SURFACE_LIFT]), yaw)


class Target:
    def __init__(self, center: np.ndarray, axes: np.ndarray, extents: np.ndarray):
        self.center = np.asarray(center, dtype=float)
        self.axes = np.asarray(axes, dtype=float)
        self.extents = np.asarray(extents, dtype=float)
        pairs = [(i, j) for i in range(3) for j in range(i + 1, 3) if abs(self.extents[i] - self.extents[j]) < 0.003]
        self.round_axis = next((3 - i - j for i, j in pairs), None) if len(pairs) == 1 else None

    def extent_along(self, direction: np.ndarray) -> float:
        projections = np.abs(self.axes.T @ direction)
        if self.round_axis is None:
            return float(projections @ self.extents)
        along = min(1.0, float(projections[self.round_axis]))
        diameter = self.extents[(self.round_axis + 1) % 3]
        return along * self.extents[self.round_axis] + math.sqrt(1.0 - along ** 2) * diameter

    def grasp_width(self, closing: np.ndarray, approach: np.ndarray) -> float:
        along = int(np.argmax(np.abs(self.axes.T @ approach) / self.extents))
        if self.round_axis is not None and along != self.round_axis:
            return self.extent_along(closing)
        projections = np.abs(self.axes.T @ closing)
        projections[along] = 0.0
        return float(projections @ self.extents)

    @property
    def height(self) -> float:
        return self.extent_along(np.array([0.0, 0.0, 1.0]))

    def contains(self, point: np.ndarray, margin: float) -> bool:
        local = self.axes.T @ (np.asarray(point) - self.center)
        return bool(np.all(np.abs(local) <= self.extents / 2 + margin))


def rotation_to_6d(rotation: np.ndarray) -> np.ndarray:
    return rotation[:, :2].T.reshape(6)


def rotation_from_6d(features: np.ndarray) -> np.ndarray:
    first = features[:3] / np.linalg.norm(features[:3])
    second = features[3:] - first * float(first @ features[3:])
    second = second / np.linalg.norm(second)
    return np.stack([first, second, np.cross(first, second)], axis=1)


def marker_center(image: np.ndarray) -> np.ndarray:
    rows, columns = np.nonzero(np.all(image == MARKER_COLOR, axis=-1))
    if not len(rows):
        return np.array([-1.0, -1.0], dtype=np.float32)
    return np.array([columns.mean() / image.shape[1], rows.mean() / image.shape[0]], dtype=np.float32)


def tool_rotation(yaw: float, pitch: float, roll: float) -> np.ndarray:
    base = quat_to_rot_matrix(euler_angles_to_quat(np.array([0.0, math.pi / 2 + pitch, yaw])))
    spin = np.array([[math.cos(roll), -math.sin(roll), 0], [math.sin(roll), math.cos(roll), 0], [0, 0, 1]])
    return base @ spin


class Warehouse:
    def __init__(self, streaming: bool = True):
        self.streaming = streaming
        self.world = World(stage_units_in_meters=1.0, physics_dt=PHYSICS_DT, rendering_dt=PHYSICS_DT)
        self.stage = get_current_stage()
        self.locations = {l["id"]: l for l in api_get("/api/locations")}
        self.products = {r["sku"]: r for r in api_get("/api/products")}
        self.boxes: list[Item] = []
        self.box_counter = 0
        self.rng = np.random.default_rng(0)
        self.held: Item | None = None
        self.held_offset = np.zeros(3)
        self.held_rotation = np.eye(3)
        self.tote: list[Item] = []
        self.views: dict[str, tuple[np.ndarray, np.ndarray, float]] = {}
        self.grasp_failure_next = False
        self.look_z = 0.38
        self.look_reach = HEAD_REACH
        self.look_height = HEAD_HEIGHT
        self.marker: np.ndarray | None = None
        self._build_environment()
        self.grip_material = self._physics_material("/World/PhysicsMaterials/grip", GRIP_FRICTION)
        self._build_conveyor()
        if args.graphics == "high":
            self._build_high_quality_environment()
        if args.scenario == "hard":
            self._build_receiving_tote()
            self._build_decor_totes()
        self._build_robot()
        self.world.reset()
        self._init_robot()
        self._build_cameras()

    def _build_environment(self) -> None:
        self.world.scene.add_default_ground_plane()
        light = UsdLux.DomeLight.Define(self.stage, "/World/DomeLight")
        light.CreateIntensityAttr(900)
        sun = UsdLux.DistantLight.Define(self.stage, "/World/Sun")
        sun.CreateIntensityAttr(1600)
        UsdGeom.Xformable(sun).AddRotateXYZOp().Set(Gf.Vec3f(-55, 0, 35))

        aisles: dict[str, list[dict]] = {}
        for location in self.locations.values():
            if location["kind"] == "storage":
                aisles.setdefault(location["aisle"], []).append(location)
        for aisle, slots in aisles.items():
            x = float(slots[0]["x"])
            ys = [float(s["y"]) for s in slots]
            y0, y1 = min(ys) - BIN_LENGTH / 2, max(ys) + BIN_LENGTH / 2
            levels = sorted({float(s["z"]) for s in slots})
            for i, z in enumerate(levels):
                FixedCuboid(
                    f"/World/Shelves/{aisle}/board_{i}", position=np.array([x, (y0 + y1) / 2, z - SHELF_BOARD / 2]),
                    scale=np.array([SHELF_DEPTH, y1 - y0, SHELF_BOARD]), color=np.array([0.55, 0.42, 0.25]),
                )
            for j, y in enumerate(np.linspace(y0, y1, len(set(s["bay"] for s in slots)) + 1)):
                for side in (-1, 1):
                    FixedCuboid(
                        f"/World/Shelves/{aisle}/post_{j}_{side + 1}",
                        position=np.array([x + side * (SHELF_DEPTH / 2 - 0.02), y, (max(levels) + 0.45) / 2]),
                        scale=np.array([0.04, 0.04, max(levels) + 0.45]), color=np.array([0.15, 0.3, 0.6]),
                    )
            for i, z in enumerate(levels):
                strip = UsdLux.RectLight.Define(self.stage, f"/World/Shelves/{aisle}/strip_{i}")
                strip.CreateWidthAttr(0.06)
                strip.CreateHeightAttr(y1 - y0)
                strip.CreateIntensityAttr(SHELF_LIGHT_INTENSITY)
                strip.CreateColorAttr(Gf.Vec3f(1.0, 0.96, 0.9))
                UsdGeom.Xformable(strip).AddTranslateOp().Set(Gf.Vec3d(x - SHELF_DEPTH / 2 + 0.06, (y0 + y1) / 2, z + 0.4))
            FixedCuboid(
                f"/World/Shelves/{aisle}/back", position=np.array([x + SHELF_DEPTH / 2, (y0 + y1) / 2, (max(levels) + 0.45) / 2]),
                scale=np.array([0.01, y1 - y0, max(levels) + 0.45]), color=np.array([0.2, 0.22, 0.25]),
            )

        zone_colors = {"dispatch": (0.15, 0.65, 0.35), "staging": (0.5, 0.5, 0.55), "charging": (0.85, 0.65, 0.15)}
        for location in self.locations.values():
            if location["kind"] in zone_colors:
                VisualCuboid(
                    f"/World/Zones/{location['id'].replace('-', '_')}",
                    position=np.array([float(location["x"]), float(location["y"]), 0.005]),
                    scale=np.array([1.4, 1.2, 0.01]), color=np.array(zone_colors[location["kind"]]),
                )
            if location["kind"] == "staging":
                FixedCuboid(
                    f"/World/Pallets/{location['id'].replace('-', '_')}",
                    position=np.array([float(location["x"]) + 0.35, float(location["y"]), STAGING_HEIGHT / 2]),
                    scale=np.array([0.6, 0.9, STAGING_HEIGHT]), color=np.array([0.6, 0.45, 0.28]),
                )

    def _physics_material(self, path: str, friction: float) -> UsdShade.Material:
        material = UsdShade.Material.Define(self.stage, path)
        api = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
        api.CreateStaticFrictionAttr(friction)
        api.CreateDynamicFrictionAttr(friction)
        api.CreateRestitutionAttr(0.0)
        return material

    def _bind_physics_material(self, root: str, material: UsdShade.Material) -> None:
        for prim in Usd.PrimRange(self.stage.GetPrimAtPath(root)):
            if prim.HasAPI(UsdPhysics.CollisionAPI):
                UsdShade.MaterialBindingAPI.Apply(prim).Bind(
                    material, UsdShade.Tokens.strongerThanDescendants, "physics")

    def _material(self, name: str, color, roughness: float, metallic: float = 0.0) -> UsdShade.Material:
        path = f"/World/Looks/{name}"
        material = UsdShade.Material.Define(self.stage, path)
        shader = UsdShade.Shader.Define(self.stage, path + "/Shader")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
        shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metallic)
        material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        return material

    def _bind(self, root: str, material: UsdShade.Material) -> None:
        prim = self.stage.GetPrimAtPath(root)
        if prim.IsValid():
            for child in [prim, *prim.GetAllChildren()]:
                UsdShade.MaterialBindingAPI.Apply(child).Bind(material, UsdShade.Tokens.strongerThanDescendants)

    def _add_prop(self, asset: str, path: str, translate, rotate_z: float, scale) -> None:
        add_reference_to_stage(f"{self.assets_root}{WAREHOUSE_PROPS}/{asset}.usd", path)
        self._place_prop(path, translate, rotate_z, scale)

    def _place_prop(self, path: str, translate, rotate_z: float, scale) -> None:
        prim = self.stage.GetPrimAtPath(path)
        xform = UsdGeom.Xformable(prim)
        xform.ClearXformOpOrder()
        double = UsdGeom.XformOp.PrecisionDouble
        precision = {name: double if prim.GetAttribute(f"xformOp:{name}").GetTypeName() in ("double3", "double") else None
                     for name in ("translate", "rotateZ", "scale")}
        xform.AddTranslateOp(precision=precision["translate"] or double).Set(tuple(map(float, translate)))
        xform.AddRotateZOp(precision=precision["rotateZ"] or UsdGeom.XformOp.PrecisionFloat).Set(float(rotate_z))
        xform.AddScaleOp(precision=precision["scale"] or UsdGeom.XformOp.PrecisionFloat).Set(tuple(map(float, scale)))
        for child in Usd.PrimRange(prim):
            if child.HasAPI(UsdPhysics.CollisionAPI):
                UsdPhysics.CollisionAPI(child).CreateCollisionEnabledAttr(False)
            if child.HasAPI(UsdPhysics.RigidBodyAPI):
                UsdPhysics.RigidBodyAPI(child).CreateRigidBodyEnabledAttr(False)

    def _slab(self, path: str, center, size, material: UsdShade.Material) -> None:
        cube = UsdGeom.Cube.Define(self.stage, path)
        cube.CreateSizeAttr(1.0)
        cube.AddTranslateOp().Set(Gf.Vec3d(*map(float, center)))
        cube.AddScaleOp().Set(Gf.Vec3f(*map(float, size)))
        UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(material)

    def _hide(self, path: str) -> None:
        UsdGeom.Imageable(self.stage.GetPrimAtPath(path)).MakeInvisible()

    def _build_props(self) -> None:
        backing = self._material("RackBacking", (0.62, 0.64, 0.66), 0.5, 0.5)
        for aisle in self.stage.GetPrimAtPath("/World/Shelves").GetChildren():
            for part in aisle.GetChildren():
                name = part.GetName()
                if name == "back":
                    self._bind(str(part.GetPath()), backing)
                    continue
                if name.startswith("strip"):
                    continue
                cube = UsdGeom.Xformable(part)
                world = cube.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                center = np.array(world.ExtractTranslation())
                size = np.array([world.GetRow3(i).GetLength() for i in range(3)])
                self._hide(str(part.GetPath()))
                target = f"{part.GetPath()}_prop"
                if name.startswith("board"):
                    self._add_prop("SM_RackShelf_01", target, center + np.array([0, 0, size[2] / 2 - 0.0075]), 90,
                                   [size[1] / RACK_SHELF_SIZE[0], size[0] / RACK_SHELF_SIZE[1], 0.3])
                elif name.startswith("post") and name.endswith("_0"):
                    self._add_prop("SM_RackFrame_03", target, center + np.array([SHELF_DEPTH / 2 - 0.02, 0, -size[2] / 2]), 90,
                                   [1.0, SHELF_DEPTH / RACK_FRAME_SIZE[1], size[2] / RACK_FRAME_SIZE[2]])
        pallets = self.stage.GetPrimAtPath("/World/Pallets")
        for part in pallets.GetChildren() if pallets.IsValid() else []:
            world = UsdGeom.Xformable(part).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            center = np.array(world.ExtractTranslation())
            size = np.array([world.GetRow3(i).GetLength() for i in range(3)])
            self._hide(str(part.GetPath()))
            self._add_prop("SM_PaletteA_01", f"{part.GetPath()}_prop", center - np.array([0, 0, size[2] / 2]), 90,
                           [size[1] / PALLET_SIZE[0], size[0] / PALLET_SIZE[1], size[2] / PALLET_SIZE[2]])

    def _build_decor_bay(self, name: str, cx: float, cy: float, along_y: bool, rng: np.random.Generator) -> None:
        rotate = 90 if along_y else 0
        axis = np.array([0.0, 1.0, 0.0]) if along_y else np.array([1.0, 0.0, 0.0])
        center = np.array([cx, cy, 0.0])
        root = f"/World/Decor/{name}"
        for side in (-1, 1):
            self._add_prop("SM_RackFrame_03", f"{root}/frame_{side + 1}", center + axis * side * DECOR_BAY / 2, rotate, (1, 1, 1))
        for level, z in enumerate(DECOR_LEVELS):
            self._add_prop("SM_RackShelf_01", f"{root}/shelf_{level}", center + np.array([0, 0, z - 0.025]), rotate, (1, 1, 1))
            if level == 0 and rng.random() < 0.6:
                for k, offset in enumerate((-1.0, 1.0)):
                    base = center + axis * offset + np.array([0, 0, z])
                    self._add_prop("SM_PaletteA_01", f"{root}/pallet_{k}", base, rotate, (0.8, 0.8, 1))
                    self._add_prop("SM_CardBoxA_01", f"{root}/load_{k}", base + np.array([0, 0, 0.211]), rotate, (1, 1, 1.4))
                continue
            along = -DECOR_BAY / 2 + 0.25
            k = 0
            while along < DECOR_BAY / 2 - 0.5:
                asset = rng.choice(list(DECOR_CARTONS))
                length = DECOR_CARTONS[asset][0]
                if rng.random() < 0.8:
                    position = center + axis * (along + length / 2) + np.array([0, 0, z])
                    self._add_prop(asset, f"{root}/carton_{level}_{k}", position, rotate, (1, 1, 1))
                    k += 1
                along += length + rng.uniform(0.05, 0.3)

    def _build_decor(self, x0: float, x1: float, y0: float, y1: float) -> None:
        rng = np.random.default_rng(7)
        for i, x in enumerate((-3.5, -6.5, -9.5)):
            for j, y in enumerate((1.5, 5.5)):
                self._build_decor_bay(f"west_{i}_{j}", x, y, True, rng)
        for i, x in enumerate(np.arange(x0 + 3.0, x1 - 6.0, DECOR_BAY)):
            self._build_decor_bay(f"north_{i}", float(x), y1 - 0.8, False, rng)

    def _build_high_quality_environment(self) -> None:
        self.assets_root = get_assets_root_path()
        self.stage.GetPrimAtPath("/World/Sun").SetActive(False)
        self.stage.GetPrimAtPath("/World/DomeLight").GetAttribute("inputs:intensity").Set(600.0)
        xs = [float(l["x"]) for l in self.locations.values()]
        ys = [float(l["y"]) for l in self.locations.values()]
        x0, x1, y0, y1 = min(xs) - 12, max(xs) + 4, min(ys) - 12, max(ys) + 4
        cx, cy, height = (x0 + x1) / 2, (y0 + y1) / 2, 6.0

        ground = self.stage.GetPrimAtPath("/World/defaultGroundPlane")
        for prim in ground.GetAllChildren() if ground.IsValid() else []:
            if prim.IsA(UsdGeom.Imageable) and not prim.HasAPI(UsdPhysics.CollisionAPI):
                UsdGeom.Imageable(prim).MakeInvisible()
        self._slab("/World/Building/floor", (cx, cy, -0.005), (x1 - x0, y1 - y0, 0.01),
                   self._material("Concrete", (0.42, 0.42, 0.40), 0.85))
        for i, tx in enumerate(np.arange(x0 + FLOOR_TILE / 2, x1 + FLOOR_TILE / 2, FLOOR_TILE)):
            for j, ty in enumerate(np.arange(y0 + FLOOR_TILE / 2, y1 + FLOOR_TILE / 2, FLOOR_TILE)):
                self._add_prop("SM_floor02", f"/World/Building/tile_{i}_{j}", (tx, ty, 0.0015), 0, (1, 1, 1))
        wall = self._material("Wall", (0.78, 0.79, 0.80), 0.7)
        for name, pos, scale in (
            ("north", (cx, y1, height / 2), (x1 - x0, 0.2, height)),
            ("east", (x1, cy, height / 2), (0.2, y1 - y0, height)),
        ):
            self._slab(f"/World/Building/wall_{name}", pos, scale, wall)

        stripe = self._material("LaneMarking", (0.95, 0.75, 0.1), 0.5)
        for i, y in enumerate((CORRIDOR_Y - 0.7, CORRIDOR_Y + 0.7)):
            self._slab(f"/World/Building/lane_{i}", (cx, y, 0.002), (x1 - x0 - 1, 0.08, 0.004), stripe)

        for i, x in enumerate(np.arange(x0 + 2, x1 - 1, 4.0)):
            for j, y in enumerate(np.arange(y0 + 2, y1 - 1, 4.0)):
                light = UsdLux.RectLight.Define(self.stage, f"/World/Lamps/lamp_{i}_{j}")
                light.CreateWidthAttr(1.2)
                light.CreateHeightAttr(0.3)
                light.CreateIntensityAttr(15000)
                light.CreateColorTemperatureAttr(5000)
                light.CreateEnableColorTemperatureAttr(True)
                UsdGeom.Xformable(light).AddTranslateOp().Set(Gf.Vec3d(float(x), float(y), height - 0.6))

        self._build_props()
        self._build_decor(x0, x1, y0, y1)

    def _build_conveyor(self) -> None:
        self.assets_root = get_assets_root_path()
        for i in range(CONVEYOR_SEGMENTS):
            add_reference_to_stage(f"{self.assets_root}/Isaac/Props/Conveyors/ConveyorBelt_A08.usd", f"/World/Conveyor/segment_{i}")
            self._place_prop(f"/World/Conveyor/segment_{i}", (CONVEYOR_X, CONVEYOR_START_Y + i * CONVEYOR_SEGMENT, 0), -90,
                             (1, 1, CONVEYOR_HEIGHT_SCALE))
        end_y = CONVEYOR_START_Y + CONVEYOR_SEGMENTS * CONVEYOR_SEGMENT
        self.curve_center = np.array([CONVEYOR_X + CURVE_PIVOT_OFFSET, end_y])
        self.curve_radius = float(self.curve_center[0] - TOTE_X)
        exit_x = self.curve_center[0]
        exit_center_y = end_y + CURVE_CENTERLINE
        self.exit_wall_x = max(float(l["x"]) for l in self.locations.values()) + 4.0 - 0.1
        add_reference_to_stage(f"{self.assets_root}/Isaac/Props/Conveyors/ConveyorBelt_A02.usd", "/World/Conveyor/curve")
        self._place_prop("/World/Conveyor/curve", (CONVEYOR_X, end_y, 0), 90, (1, 1, CONVEYOR_HEIGHT_SCALE))
        add_reference_to_stage(f"{self.assets_root}/Isaac/Props/Conveyors/ConveyorBelt_A08.usd", "/World/Conveyor/exit")
        self._place_prop("/World/Conveyor/exit", (exit_x, exit_center_y, 0), 180,
                         ((self.exit_wall_x - exit_x) / CONVEYOR_SEGMENT, 1, CONVEYOR_HEIGHT_SCALE))
        VisualCuboid("/World/Conveyor/exit_opening", position=np.array([self.exit_wall_x, exit_center_y, ROLLER_TOP + 0.2]),
                     scale=np.array([0.02, CONVEYOR_WIDTH, 0.45]), color=np.array([0.03, 0.03, 0.035]))
        self.conveyor_end_y = end_y
        for prim in Usd.PrimRange(self.stage.GetPrimAtPath("/World/Conveyor")):
            if prim.GetName().startswith(CONVEYOR_HIDDEN_PARTS) and prim.IsA(UsdGeom.Imageable):
                UsdGeom.Imageable(prim).MakeInvisible()
        self.dispatch_totes: dict[str, str] = {}
        self.shipping: list[tuple[str, list[Item]]] = []
        self.tote_counter = 0
        self.tote_material = self._material("ToteGrey", (0.3, 0.31, 0.33), 0.6)
        for location in self.locations.values():
            if location["kind"] == "dispatch":
                self.dispatch_totes[location["id"]] = self._spawn_tote(float(location["y"]))
        self.reset_dispatch_totes()

    def _spawn_tote(self, y: float) -> str:
        self.tote_counter += 1
        path = f"/World/Totes/tote_{self.tote_counter}"
        add_reference_to_stage(f"{self.assets_root}/Isaac/Props/KLT_Bin/small_KLT_visual.usd", path)
        self._place_prop(path, (TOTE_X, y, ROLLER_TOP + TOTE_SIZE[2] / 2), 0, TOTE_SCALE)
        for child in Usd.PrimRange(self.stage.GetPrimAtPath(path)):
            UsdShade.MaterialBindingAPI.Apply(child).Bind(self.tote_material, UsdShade.Tokens.strongerThanDescendants)
        return path

    def ship_tote(self, location_id: str) -> None:
        path = self.dispatch_totes[location_id]
        tote_y = float(self.locations[location_id]["y"])
        boxes = []
        for box in [b for b in self.boxes if b.location_id == location_id]:
            box.location_id = None
            boxes.append((box, box.position - np.array([TOTE_X, tote_y, 0.0]), box.yaw))
        self.shipping.append((path, boxes))
        self.dispatch_totes[location_id] = self._spawn_tote(float(self.locations[location_id]["y"]))
        self._hide(self.dispatch_totes[location_id])

    def update_conveyor(self) -> None:
        step = TOTE_SPEED * PHYSICS_DT
        arrived = []
        for path, boxes in self.shipping:
            ops = UsdGeom.Xformable(self.stage.GetPrimAtPath(path)).GetOrderedXformOps()
            position = ops[0].Get()
            x, y = float(position[0]), float(position[1])
            cx, cy = self.curve_center
            if y < cy and x < cx:
                y = min(y + step, cy)
                yaw = 0.0
            elif x < cx:
                angle = math.atan2(y - cy, x - cx) - step / self.curve_radius
                if angle <= math.pi / 2:
                    x, y, yaw = cx, cy + self.curve_radius, -math.pi / 2
                else:
                    x, y = cx + self.curve_radius * math.cos(angle), cy + self.curve_radius * math.sin(angle)
                    yaw = angle - math.pi
            else:
                x += step
                yaw = -math.pi / 2
            ops[0].Set(Gf.Vec3d(x, y, position[2]))
            ops[1].Set(float(math.degrees(yaw)))
            turn = np.array([[math.cos(yaw), -math.sin(yaw), 0.0], [math.sin(yaw), math.cos(yaw), 0.0], [0.0, 0.0, 1.0]])
            for box, offset, start_yaw in boxes:
                box.move(np.array([x, y, 0.0]) + turn @ offset, start_yaw + yaw)
            if x + TOTE_SIZE[1] / 2 > self.exit_wall_x:
                arrived.append((path, boxes))
        for path, boxes in arrived:
            self.shipping.remove((path, boxes))
            self.stage.RemovePrim(path)
            for box, _, _ in boxes:
                self.stage.RemovePrim(box.path)
                self.boxes.remove(box)

    def upstream_dispatch(self) -> str:
        return min(self.dispatch_totes, key=lambda location_id: float(self.locations[location_id]["y"]))

    def reset_dispatch_totes(self) -> None:
        for location_id, path in self.dispatch_totes.items():
            if location_id == self.upstream_dispatch():
                UsdGeom.Imageable(self.stage.GetPrimAtPath(path)).MakeVisible()
            else:
                self._hide(path)

    def show_tote(self, location_id: str) -> None:
        UsdGeom.Imageable(self.stage.GetPrimAtPath(self.dispatch_totes[location_id])).MakeVisible()

    def _build_robot(self) -> None:
        root = get_assets_root_path()
        add_reference_to_stage(root + "/Isaac/Robots/Clearpath/RidgebackFranka/ridgeback_franka.usd", "/World/Robot")
        anchor = UsdPhysics.FixedJoint.Define(self.stage, "/World/Robot/world_anchor")
        anchor.CreateBody1Rel().SetTargets(["/World/Robot/world"])
        self.robot = self.world.scene.add(SingleArticulation("/World/Robot", name="robot"))
        self.tote_stand = VisualCuboid(
            "/World/ToteStand", position=np.array([0.0, 0.0, 0.3]),
            scale=np.array([0.07, 0.07, TOTE_TOP - DECK_HEIGHT]), color=MOUNT_METAL,
        )
        self.hardware = [VisualCuboid(f"/World/Mounts/part_{i}", position=np.zeros(3), scale=np.array(size), color=color)
                         for i, (_, _, size, color) in enumerate(HARDWARE)]
        self._build_tools()
        self._build_robot_tote()

    def _build_robot_tote(self) -> None:
        root = "/World/RobotTote"
        UsdGeom.Xform.Define(self.stage, root)
        self.robot_tote_material = self._material("RobotToteBlue", (0.16, 0.32, 0.62), 0.6)
        add_reference_to_stage(f"{self.assets_root}/Isaac/Props/KLT_Bin/small_KLT_visual.usd", f"{root}/visual")
        self._place_prop(f"{root}/visual", (0.0, 0.0, ROBOT_TOTE_SIZE[2] / 2), 0, ROBOT_TOTE_SCALE)
        for child in Usd.PrimRange(self.stage.GetPrimAtPath(f"{root}/visual")):
            UsdShade.MaterialBindingAPI.Apply(child).Bind(self.robot_tote_material, UsdShade.Tokens.strongerThanDescendants)
        length, width, height = ROBOT_TOTE_SIZE
        wall = ROBOT_TOTE_WALL
        walls = {
            "floor": ((0, 0, -wall / 2 + TOTE_WALL), (length, width, wall)),
            "front": ((-(length + wall) / 2, 0, height / 2), (wall, width, height)),
            "back": (((length + wall) / 2, 0, height / 2), (wall, width, height)),
            "left": ((0, -(width + wall) / 2, height / 2), (length + 2 * wall, wall, height)),
            "right": ((0, (width + wall) / 2, height / 2), (length + 2 * wall, wall, height)),
        }
        for name, (offset, extent) in walls.items():
            cube = UsdGeom.Cube.Define(self.stage, f"{root}/{name}")
            cube.CreateSizeAttr(1.0)
            cube.AddTranslateOp().Set(Gf.Vec3d(*map(float, offset)))
            cube.AddScaleOp().Set(Gf.Vec3f(*map(float, extent)))
            cube.MakeInvisible()
            UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
        self.make_deck_collider(root)
        self.tote_prim = SingleXFormPrim(root)

    def _build_tools(self) -> None:
        self.tool: str | None = None
        self.push_encoding = False
        self.trail: list[np.ndarray] | None = None
        self.tool_prims: dict[str, list[SingleXFormPrim]] = {}
        robot_parts = ["/World/Robot"] + [str(prim.GetPath()) for prim in Usd.PrimRange(self.stage.GetPrimAtPath("/World/Robot"))
                                          if prim.HasAPI(UsdPhysics.RigidBodyAPI)]
        for name, parts in TOOL_PARTS.items() if SHOW_TOOLS else ():
            prims = []
            for i, (_, size, collides) in enumerate(parts):
                path = f"/World/Tools/{name}_{i}"
                VisualCuboid(path, position=np.zeros(3), scale=np.array(size), color=np.array(TOOL_COLORS[name]))
                prim = self.stage.GetPrimAtPath(path)
                if collides:
                    UsdPhysics.CollisionAPI.Apply(prim)
                    UsdPhysics.RigidBodyAPI.Apply(prim).CreateKinematicEnabledAttr(True)
                    filtered = UsdPhysics.FilteredPairsAPI.Apply(prim).CreateFilteredPairsRel()
                    for target in robot_parts:
                        filtered.AddTarget(target)
                prims.append(SingleXFormPrim(path))
            self.tool_prims[name] = prims

    def make_deck_collider(self, path: str) -> None:
        prim = self.stage.GetPrimAtPath(path)
        UsdPhysics.RigidBodyAPI.Apply(prim).CreateKinematicEnabledAttr(True)
        filtered = UsdPhysics.FilteredPairsAPI.Apply(prim).CreateFilteredPairsRel()
        for robot_part in Usd.PrimRange(self.stage.GetPrimAtPath("/World/Robot")):
            if robot_part.HasAPI(UsdPhysics.RigidBodyAPI):
                filtered.AddTarget(robot_part.GetPath())

    def tool_frame(self, name: str) -> tuple[np.ndarray, np.ndarray]:
        if self.tool == name:
            position, orientation = self.hand.get_world_pose()
            rotation = quat_to_rot_matrix(orientation)
            return position + rotation @ FINGERTIP_OFFSET, rotation @ self.gripper_frame @ TOOL_MOUNTS[name]
        return self.tool_slot(name)

    def tool_slot(self, name: str) -> tuple[np.ndarray, np.ndarray]:
        x, y, theta = self.base_pose()
        turn = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
        xy = np.array([x, y]) + turn @ (TOOLBOX_OFFSET + TOOL_SLOTS[name])
        top = TOOLBOX_TOP
        return np.array([xy[0], xy[1], top + TOOL_STOW_HEIGHT[name]]), tool_rotation(theta, math.pi / 2, 0.0)

    def update_tools(self) -> None:
        for name, parts in TOOL_PARTS.items():
            if name not in self.tool_prims:
                continue
            origin, rotation = self.tool_frame(name)
            orientation = rot_matrix_to_quat(rotation)
            for prim, (center, _, _) in zip(self.tool_prims[name], parts):
                prim.set_world_pose(origin + rotation @ np.array(center, dtype=float), orientation)

    def tool_tip(self) -> np.ndarray:
        return TOOL_MOUNTS[self.tool] @ TOOL_TIPS[self.tool] if self.tool else np.zeros(3)

    def _init_robot(self) -> None:
        names = self.robot.dof_names
        self.base_idx = np.array([names.index(n) for n in (
            "dummy_base_prismatic_x_joint", "dummy_base_prismatic_y_joint", "dummy_base_revolute_z_joint")])
        self.arm_idx = np.array([names.index(f"panda_joint{i}") for i in range(1, 8)])
        self.finger_idx = np.array([names.index("panda_finger_joint1"), names.index("panda_finger_joint2")])
        self.arm_home = np.array([0.0, -0.6, 0.0, -2.4, 0.0, 1.9, 0.785])
        self.work_home = np.array([0.0, -0.4, 0.0, -2.2, 0.0, 1.8, 0.785])
        config = interface_config_loader.load_supported_lula_kinematics_solver_config("Franka")
        self.ik = LulaKinematicsSolver(**config)
        self.ik_articulation = ArticulationKinematicsSolver(self.robot, self.ik, "right_gripper")
        for finger in ("panda_leftfinger", "panda_rightfinger"):
            self._bind_physics_material(f"/World/Robot/{finger}", self.grip_material)
        for prim in Usd.PrimRange(self.stage.GetPrimAtPath("/World/Robot")):
            if prim.GetName() in ("panda_finger_joint1", "panda_finger_joint2"):
                drive = UsdPhysics.DriveAPI.Get(prim, "linear")
                print("FINGER_DRIVE", prim.GetPath(), drive.GetStiffnessAttr().Get(), drive.GetDampingAttr().Get(),
                      drive.GetMaxForceAttr().Get(), flush=True)
                drive.GetStiffnessAttr().Set(FINGER_STIFFNESS)
                drive.GetDampingAttr().Set(FINGER_DAMPING)
                drive.GetMaxForceAttr().Set(FINGER_MAX_FORCE)
        self.link0 = SingleXFormPrim("/World/Robot/panda_link0")
        _, hand_rotation = self.ik.compute_forward_kinematics("panda_hand", self.arm_home)
        _, gripper_rotation = self.ik.compute_forward_kinematics("right_gripper", self.arm_home)
        self.gripper_frame = np.asarray(hand_rotation).T @ np.asarray(gripper_rotation)
        print("GRIPPER_FRAME", np.round(self.gripper_frame, 3).tolist(), flush=True)
        self.hand = SingleXFormPrim("/World/Robot/panda_hand")
        dock = self.locations.get("DOCK-1")
        start = np.array([float(dock["x"]), float(dock["y"]), 0.0]) if dock else np.zeros(3)
        positions = self.robot.get_joint_positions()
        positions[self.base_idx] = start
        positions[self.arm_idx] = self.arm_home
        positions[self.finger_idx] = 0.04
        self.robot.set_joint_positions(positions)
        self.base_target = start.copy()
        self.arm_target = self.arm_home.copy()
        self.finger_target = 0.04

    def _build_policy_cameras(self) -> None:
        camera = UsdGeom.Camera.Define(self.stage, "/World/HeadCam")
        camera.CreateFocalLengthAttr(HEAD_FOCAL)
        camera.CreateHorizontalApertureAttr(20.955)
        camera.CreateVerticalApertureAttr(20.955)
        annotator = rep.AnnotatorRegistry.get_annotator("rgb")
        annotator.attach([rep.create.render_product("/World/HeadCam", POLICY_RES)])
        self.annotators["head"] = annotator
        wrist = UsdGeom.Camera.Define(self.stage, "/World/WristCam")
        wrist.CreateFocalLengthAttr(WRIST_FOCAL)
        wrist.CreateHorizontalApertureAttr(20.955)
        wrist.CreateVerticalApertureAttr(20.955)
        wrist.CreateClippingRangeAttr(Gf.Vec2f(0.01, 100.0))
        annotator = rep.AnnotatorRegistry.get_annotator("rgb")
        annotator.attach([rep.create.render_product("/World/WristCam", POLICY_RES)])
        self.annotators["wrist"] = annotator
        self.update_policy_cameras()
        perception = rep.create.render_product("/World/HeadCam", PERCEPTION_RES)
        for name in ("distance_to_image_plane", "instance_id_segmentation_fast"):
            annotator = rep.AnnotatorRegistry.get_annotator(name)
            annotator.attach([perception])
            self.annotators[name] = annotator

    def base_rotation(self) -> np.ndarray:
        theta = self.base_pose()[2]
        return np.array([[math.cos(theta), -math.sin(theta), 0], [math.sin(theta), math.cos(theta), 0], [0, 0, 1]])

    def to_base_frame(self, point: np.ndarray) -> np.ndarray:
        x, y, _ = self.base_pose()
        return self.base_rotation().T @ (point - np.array([x, y, 0.0]))

    def to_world_frame(self, point: np.ndarray) -> np.ndarray:
        x, y, _ = self.base_pose()
        return self.base_rotation() @ point + np.array([x, y, 0.0])

    def tool_pose(self) -> np.ndarray:
        _, orientation = self.hand.get_world_pose()
        rotation = quat_to_rot_matrix(orientation)
        approach = self.base_rotation().T @ rotation[:, 2]
        yaw = math.atan2(approach[1], approach[0])
        pitch = math.asin(float(np.clip(-approach[2], -1.0, 1.0)))
        spin = tool_rotation(yaw + self.base_pose()[2], pitch, 0.0).T @ rotation
        roll = math.atan2(spin[1, 0], spin[0, 0])
        return np.concatenate([self.to_base_frame(self.fingertip()), [yaw, pitch, roll]])

    def push_pose(self) -> np.ndarray:
        _, orientation = self.hand.get_world_pose()
        rotation = self.base_rotation().T @ quat_to_rot_matrix(orientation)
        return np.concatenate([self.to_base_frame(self.fingertip()), rotation_to_6d(rotation)])

    def push_orientation(self, features: np.ndarray) -> np.ndarray:
        return rot_matrix_to_quat(self.base_rotation() @ rotation_from_6d(np.asarray(features)) @ self.gripper_frame)

    def tool_orientation(self, yaw: float, pitch: float, roll: float = 0.0) -> np.ndarray:
        rotation = quat_to_rot_matrix(euler_angles_to_quat(np.array([0.0, math.pi / 2 + pitch, self.base_pose()[2] + yaw])))
        spin = np.array([[math.cos(roll), -math.sin(roll), 0], [math.sin(roll), math.cos(roll), 0], [0, 0, 1]])
        return rot_matrix_to_quat(rotation @ spin)

    def head_pixel(self, point: np.ndarray) -> np.ndarray | None:
        transform = self.prim_transform("/World/HeadCam")
        camera = (point - transform[3, :3]) @ transform[:3, :3].T
        if camera[2] >= 0:
            return None
        focal = POLICY_RES[0] * HEAD_FOCAL / 20.955
        return np.array([POLICY_RES[0] / 2 + focal * camera[0] / -camera[2], POLICY_RES[1] / 2 - focal * camera[1] / -camera[2]])

    def draw_marker(self, image: np.ndarray) -> np.ndarray:
        if self.marker is None:
            return image
        pixel = self.head_pixel(self.marker)
        if pixel is None:
            return image
        rows, columns = np.ogrid[:image.shape[0], :image.shape[1]]
        distance = np.sqrt((columns - pixel[0]) ** 2 + (rows - pixel[1]) ** 2)
        image[(distance > MARKER_RADIUS - 1.5) & (distance < MARKER_RADIUS + 1.0)] = MARKER_COLOR
        return image

    def policy_observation(self) -> dict:
        opening = float(self.robot.get_joint_positions()[self.finger_idx].mean())
        pose = self.push_pose() if self.push_encoding else self.tool_pose()
        state = np.concatenate([pose, [opening]]).astype(np.float32)
        return {
            "head": self.draw_marker(self.annotators["head"].get_data()[..., :3].copy()),
            "wrist": self.annotators["wrist"].get_data()[..., :3].copy(),
            "state": state,
            "finger_target": np.float32(self.finger_target),
        }

    def update_policy_cameras(self) -> None:
        x, y, theta = self.base_pose()
        work_side = np.array([math.cos(theta), math.sin(theta), 0.0])
        mount = np.array([x, y, 0.0]) + np.array([math.cos(theta), math.sin(theta), 0.0]) * ARM_MOUNT
        set_camera_view(eye=mount + np.array([0, 0, self.look_height]),
                        target=mount + work_side * self.look_reach + np.array([0, 0, self.look_z]), camera_prim_path="/World/HeadCam")
        hand_position, hand_orientation = self.hand.get_world_pose()
        hand = quat_to_rot_matrix(hand_orientation)
        tip = hand_position + hand @ FINGERTIP_OFFSET
        set_camera_view(eye=hand_position + hand @ WRIST_OFFSET, target=tip + hand[:, 2] * WRIST_LOOK,
                        camera_prim_path="/World/WristCam")

    def visible_points(self, box: Item) -> np.ndarray:
        depth = np.asarray(self.annotators["distance_to_image_plane"].get_data(), dtype=float).squeeze()
        segmentation = self.annotators["instance_id_segmentation_fast"].get_data()
        labels = segmentation["info"]["idToLabels"]
        ids = [int(i) for i, path in labels.items() if str(path) == box.path or str(path).startswith(box.path + "/")]
        mask = np.isin(segmentation["data"], ids) & np.isfinite(depth) & (depth > 0)
        rows, columns = np.nonzero(mask)
        d = depth[rows, columns]
        height, width = depth.shape
        focal = width * HEAD_FOCAL / 20.955
        camera = np.stack([(columns + 0.5 - width / 2) / focal * d, -(rows + 0.5 - height / 2) / focal * d, -d, np.ones_like(d)], axis=1)
        transform = np.array(UsdGeom.Xformable(self.stage.GetPrimAtPath("/World/HeadCam")).ComputeLocalToWorldTransform(Usd.TimeCode.Default()))
        return (camera @ transform)[:, :3]

    def prim_transform(self, path: str) -> np.ndarray:
        matrix = UsdGeom.Xformable(self.stage.GetPrimAtPath(path)).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        return np.array([[matrix[i][j] for j in range(4)] for i in range(4)], dtype=float)

    def true_target(self, box: Item) -> Target:
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
        local = cache.ComputeUntransformedBound(self.stage.GetPrimAtPath(box.path)).ComputeAlignedRange()
        transform = self.prim_transform(box.path)
        rotation = transform[:3, :3]
        scale = np.linalg.norm(rotation, axis=1)
        center = np.array(list(local.GetMidpoint()), dtype=float) @ rotation + transform[3, :3]
        measured = np.array(list(local.GetSize()), dtype=float) * scale
        extents = np.empty(3)
        extents[np.argsort(measured)] = np.sort(box.size)
        return Target(center, (rotation / scale[:, None]).T, extents)

    def surface_z(self, location_id: str) -> float:
        location = self.locations[location_id]
        if location["kind"] == "staging":
            return STAGING_HEIGHT + (TOTE_WALL if args.scenario == "hard" else 0.0)
        return float(location["z"])

    def estimate_target(self, box: Item, surface_z: float) -> Target:
        points = self.visible_points(box)
        if len(points) < MIN_VISIBLE_POINTS:
            raise SimError("not_visible", f"only {len(points)} depth points on {box.sku or box.ycb}")
        height = float(np.percentile(points[:, 2], 98)) - surface_z
        planar = points[:, :2] - points[:, :2].mean(axis=0)
        eye = self.prim_transform("/World/HeadCam")[3, :2]
        view = points[:, :2].mean(axis=0) - eye
        axes = np.linalg.eigh(np.cov(planar.T))[1].T
        normal = max(axes, key=lambda axis: abs(float(axis @ view)))
        normal = normal if normal @ view > 0 else -normal
        face_axis = np.array([-normal[1], normal[0]])
        along_normal, along_face = points[:, :2] @ normal, points[:, :2] @ face_axis
        width = float(np.percentile(along_face, 98) - np.percentile(along_face, 2))
        dims = list(box.size)
        dims.remove(min(dims, key=lambda d: abs(d - height)))
        matched_width = min(dims, key=lambda d: abs(d - width))
        dims.remove(matched_width)
        center_planar = (np.percentile(along_normal, 2) + dims[0] / 2) * normal + float(np.median(along_face)) * face_axis
        axes3 = np.array([[normal[0], face_axis[0], 0.0], [normal[1], face_axis[1], 0.0], [0.0, 0.0, 1.0]])
        return Target(np.array([*center_planar, surface_z + height / 2]), axes3, np.array([dims[0], matched_width, height]))

    def fingertip(self) -> np.ndarray:
        position, orientation = self.hand.get_world_pose()
        rotation = quat_to_rot_matrix(orientation)
        return position + rotation @ FINGERTIP_OFFSET + rotation @ self.gripper_frame @ self.tool_tip()

    def _build_cameras(self) -> None:
        self.annotators = {}
        self._build_policy_cameras()
        if not self.streaming:
            self.view = "head"
            return
        overview = UsdGeom.Camera.Define(self.stage, "/World/OverviewCam")
        overview.CreateFocalLengthAttr(18.0)
        self.reset_orbit()
        if args.livestream:
            from omni.kit.viewport.utility import get_active_viewport

            get_active_viewport().camera_path = "/World/OverviewCam"
        chase = UsdGeom.Camera.Define(self.stage, "/World/ChaseCam")
        chase.CreateFocalLengthAttr(16.0)
        inspect = UsdGeom.Camera.Define(self.stage, "/World/InspectCam")
        inspect.CreateFocalLengthAttr(11.0)
        inspect.CreateHorizontalApertureAttr(INSPECT_APERTURE)
        inspect.CreateVerticalApertureAttr(INSPECT_APERTURE * INSPECT_RES[1] / INSPECT_RES[0])
        self.view = "overview"
        self.products_rp = {
            "overview": rep.create.render_product("/World/OverviewCam", (args.width, args.height)),
            "chase": rep.create.render_product("/World/ChaseCam", (args.width, args.height)),
            "inspect": rep.create.render_product("/World/InspectCam", INSPECT_RES),
        }
        for name, rp in self.products_rp.items():
            annotator = rep.AnnotatorRegistry.get_annotator("rgb")
            annotator.attach([rp])
            self.annotators[name] = annotator

    def reset_orbit(self) -> None:
        self.orbit_target = np.array([6.0, 3.0, 0.0])
        offset = np.array([-4.0, -7.5, 9.5]) - self.orbit_target
        self.orbit_distance = float(np.linalg.norm(offset))
        self.orbit_yaw = math.atan2(offset[1], offset[0])
        self.orbit_pitch = math.asin(offset[2] / self.orbit_distance)
        self.apply_orbit()

    def apply_orbit(self) -> None:
        self.orbit_pitch = float(np.clip(self.orbit_pitch, 0.05, 1.5))
        self.orbit_distance = float(np.clip(self.orbit_distance, 1.0, 40.0))
        direction = np.array([math.cos(self.orbit_pitch) * math.cos(self.orbit_yaw),
                              math.cos(self.orbit_pitch) * math.sin(self.orbit_yaw), math.sin(self.orbit_pitch)])
        set_camera_view(eye=self.orbit_target + direction * self.orbit_distance, target=self.orbit_target,
                        camera_prim_path="/World/OverviewCam")

    def move_camera(self, payload: dict) -> None:
        if payload.get("reset"):
            return self.reset_orbit()
        if "target" in payload:
            self.orbit_target = np.array(payload["target"], dtype=float)
        self.orbit_yaw = math.radians(payload.get("yaw", math.degrees(self.orbit_yaw)))
        self.orbit_pitch = math.radians(payload.get("pitch", math.degrees(self.orbit_pitch)))
        self.orbit_distance = float(payload.get("distance", self.orbit_distance))
        dyaw, dpitch = payload.get("orbit", (0.0, 0.0))
        self.orbit_yaw -= float(dyaw)
        self.orbit_pitch += float(dpitch)
        dx, dy = payload.get("pan", (0.0, 0.0))
        right = np.array([-math.sin(self.orbit_yaw), math.cos(self.orbit_yaw), 0.0])
        forward = np.array([-math.cos(self.orbit_yaw), -math.sin(self.orbit_yaw), 0.0])
        self.orbit_target = self.orbit_target + (-float(dx) * right + float(dy) * forward) * self.orbit_distance
        self.orbit_distance *= float(payload.get("zoom", 1.0))
        self.apply_orbit()
        self.view = "overview"

    def grab(self, box: Item) -> None:
        hand_position, hand_orientation = self.hand.get_world_pose()
        box_position, box_orientation = box.prim.get_world_pose()
        hand_rotation = quat_to_rot_matrix(hand_orientation)
        self.held_offset = hand_rotation.T @ (box_position - hand_position)
        self.held_rotation = hand_rotation.T @ quat_to_rot_matrix(box_orientation)
        self.held = box

    def debug(self) -> dict:
        from pxr import Usd
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
        bound = cache.ComputeWorldBound(self.stage.GetPrimAtPath("/World/Robot")).ComputeAlignedRange()
        return {
            "robot_bbox": [list(bound.GetMin()), list(bound.GetMax())],
            "conveyor_bbox": (lambda r: [list(r.GetMin()), list(r.GetMax())])(
                cache.ComputeWorldBound(self.stage.GetPrimAtPath("/World/Conveyor")).ComputeAlignedRange()),
            "base_link": [v.tolist() for v in SingleXFormPrim("/World/Robot/base_link").get_world_pose()],
            "link0": [v.tolist() for v in self.link0.get_world_pose()],
            "hand": [v.tolist() for v in self.hand.get_world_pose()],
            "joints": dict(zip(self.robot.dof_names, self.robot.get_joint_positions().tolist())),
            "items": [{"sku": b.sku, "location_id": b.location_id, "position": np.round(b.prim.get_world_pose()[0], 3).tolist()}
                      for b in self.boxes if b.sku],
        }

    def base_pose(self) -> np.ndarray:
        return self.robot.get_joint_positions()[self.base_idx]

    def apply_targets(self) -> None:
        if self.trail is not None:
            self.trail.append(self.arm_target.copy())
        finger = max(self.finger_target, TOOL_GRIP[self.tool]) if self.tool else self.finger_target
        positions = np.concatenate([self.base_target, self.arm_target, [finger, finger]])
        indices = np.concatenate([self.base_idx, self.arm_idx, self.finger_idx])
        self.robot.apply_action(ArticulationAction(joint_positions=positions, joint_indices=indices))

    def update_attachments(self) -> None:
        self.update_tools()
        if self.held:
            position, orientation = self.hand.get_world_pose()
            hand_rotation = quat_to_rot_matrix(orientation)
            self.held.position = position + hand_rotation @ self.held_offset
            self.held.prim.set_world_pose(self.held.position, rot_matrix_to_quat(hand_rotation @ self.held_rotation))
        x, y, theta = self.base_pose()
        rotation = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
        tote_xy = np.array([x, y]) + rotation @ TOTE_OFFSET[:2]
        self.tote_prim.set_world_pose(np.array([tote_xy[0], tote_xy[1], TOTE_TOP]),
                                      euler_angles_to_quat(np.array([0, 0, theta])))
        stand_xy = np.array([x, y]) + rotation @ TOTE_STAND_OFFSET
        self.tote_stand.set_world_pose(np.array([stand_xy[0], stand_xy[1], (TOTE_TOP + DECK_HEIGHT) / 2]),
                                       euler_angles_to_quat(np.array([0, 0, theta])))
        for prim, (offset, z, _, _) in zip(self.hardware, HARDWARE):
            xy = np.array([x, y]) + rotation @ np.array(offset)
            prim.set_world_pose(np.array([xy[0], xy[1], z]), euler_angles_to_quat(np.array([0, 0, theta])))
        for box in self.tote:
            if box.pinned:
                surface, yaw = self.deck_slot(box.deck_slot)
                box.rest_on(surface, yaw)

    def deck_slot(self, index: int) -> tuple[np.ndarray, float]:
        x, y, theta = self.base_pose()
        rotation = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
        local = TOTE_OFFSET[:2] + DECK_SLOTS[index % len(DECK_SLOTS)]
        xy = np.array([x, y]) + rotation @ local
        radial = local - np.array([ARM_MOUNT, 0.0])
        return np.array([xy[0], xy[1], TOTE_TOP + TOTE_WALL]), theta + math.atan2(radial[1], radial[0])

    def free_deck_slot(self) -> int:
        used = {box.deck_slot for box in self.tote}
        return next((i for i in range(len(DECK_SLOTS)) if i not in used), len(self.tote))

    def update_chase_camera(self) -> None:
        x, y, theta = self.base_pose()
        eye = np.array([x - 2.6 * math.cos(theta), y - 2.6 * math.sin(theta) - 1.2, 2.4])
        set_camera_view(eye=eye, target=np.array([x + 0.6 * math.cos(theta), y + 0.6 * math.sin(theta), 0.6]),
                        camera_prim_path="/World/ChaseCam")

    def spawn_item(self, sku: str | None, location_id: str | None, ycb: str | None = None) -> Item:
        self.box_counter += 1
        path = f"/World/Items/item_{self.box_counter}"
        ycb = ycb or self.products[sku]["ycb_object"]
        add_reference_to_stage(f"{self.assets_root}{YCB_ROOT}/{ycb}.usd", path)
        root = self.stage.GetPrimAtPath(path)
        body = UsdPhysics.RigidBodyAPI.Apply(root)
        body.CreateKinematicEnabledAttr(False)
        body.CreateRigidBodyEnabledAttr(False)
        physx_body = PhysxSchema.PhysxRigidBodyAPI.Apply(root)
        physx_body.CreateSolverPositionIterationCountAttr(ITEM_SOLVER_ITERATIONS)
        physx_body.CreateEnableCCDAttr(True)
        UsdPhysics.MassAPI.Apply(root).CreateMassAttr(float(self.products[sku]["weight_kg"]) if sku else BACKGROUND_MASS)
        for prim in Usd.PrimRange(root):
            if prim.IsA(UsdGeom.Mesh):
                UsdPhysics.CollisionAPI.Apply(prim).CreateCollisionEnabledAttr(False)
                UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr("convexHull")
        self._bind_physics_material(path, self.grip_material)
        box = Item(SingleXFormPrim(path), sku, ycb)
        box.location_id = location_id
        self.boxes.append(box)
        return box

    def slot_surfaces(self, location_id: str, count: int) -> list[np.ndarray]:
        location = self.locations[location_id]
        x, y = float(location["x"]), float(location["y"])
        if location["kind"] == "dispatch":
            return [np.array([TOTE_X - 0.07 + (i // 5 % 2) * 0.13, y + (i % 5 - 2) * 0.09, ROLLER_TOP + 0.015])
                    for i in range(count)]
        columns, spacing = 8, 0.11
        y0 = y - (columns - 1) * spacing / 2
        return [np.array([x + 0.12 + (i // columns) * 0.14, y0 + (i % columns) * spacing, STAGING_HEIGHT])
                for i in range(count)]

    def free_spot(self, location_id: str, box: Item, front: bool) -> tuple[np.ndarray, float]:
        location = self.locations[location_id]
        x, y, z = float(location["x"]), float(location["y"]), float(location["z"])
        front_x = x - SHELF_DEPTH / 2 + BIN_MARGIN
        x_range = (front_x, front_x + BIN_FRONT_BAND) if front else (front_x + BIN_FRONT_BAND + 0.06, x + SHELF_DEPTH / 2 - BIN_MARGIN)
        half = BIN_LENGTH / 2 - 0.1
        others = [b for b in self.boxes_at(location_id, background=True) if b is not box]
        for _ in range(200):
            candidate = np.array([self.rng.uniform(*x_range) + (box.size[0] / 2 if front else 0.0),
                                  self.rng.uniform(y - half + box.radius, y + half - box.radius), z])
            if all(np.linalg.norm(candidate[:2] - o.position[:2]) > box.radius + o.radius + ITEM_GAP for o in others):
                yaw = self.rng.uniform(-CATALOG_YAW, CATALOG_YAW) if front else self.rng.uniform(-math.pi, math.pi)
                return candidate, yaw
        raise SimError("bin_full", f"no free space in {location_id}")

    def _build_tote(self, root: str, base: np.ndarray, scale: np.ndarray, colliders: bool) -> np.ndarray:
        size = np.array([0.198, 0.296, 0.146]) * scale
        add_reference_to_stage(f"{self.assets_root}/Isaac/Props/KLT_Bin/small_KLT_visual.usd", f"{root}/visual")
        self._place_prop(f"{root}/visual", (base[0], base[1], base[2] + size[2] / 2), 0, scale)
        for child in Usd.PrimRange(self.stage.GetPrimAtPath(f"{root}/visual")):
            UsdShade.MaterialBindingAPI.Apply(child).Bind(self.tote_material, UsdShade.Tokens.strongerThanDescendants)
        if colliders:
            walls = {
                "floor": ((0, 0, TOTE_WALL / 2), (size[0], size[1], TOTE_WALL)),
                "front": ((-size[0] / 2, 0, size[2] / 2), (TOTE_WALL, size[1], size[2])),
                "back": ((size[0] / 2, 0, size[2] / 2), (TOTE_WALL, size[1], size[2])),
                "left": ((0, -size[1] / 2, size[2] / 2), (size[0], TOTE_WALL, size[2])),
                "right": ((0, size[1] / 2, size[2] / 2), (size[0], TOTE_WALL, size[2])),
            }
            for name, (offset, extent) in walls.items():
                FixedCuboid(f"{root}/{name}", position=base + np.array(offset), scale=np.array(extent), size=1.0, visible=False)
        return size

    def _build_receiving_tote(self) -> None:
        location = self.locations[TOTE_BIN]
        self.receiving_tote = np.array([self.face_x(TOTE_BIN) + RECEIVING_TOTE_INSET + RECEIVING_TOTE_SIZE[0] / 2,
                                        float(location["y"]), STAGING_HEIGHT])
        self._build_tote("/World/ReceivingTote", self.receiving_tote, RECEIVING_TOTE_SCALE, colliders=True)

    def _build_decor_totes(self) -> None:
        for i, (x, y) in enumerate(DECOR_PALLETS):
            self._add_prop("SM_PaletteA_01", f"/World/DecorTotes/pallet_{i}", (x, y, 0.0), 90, (1, 1, 1))
        self.decor_totes = [np.array(base) for base in DECOR_FILLED_TOTES]
        for i, base in enumerate(self.decor_totes):
            self._build_tote(f"/World/DecorTotes/filled_{i}", base, DECOR_TOTE_SCALE, colliders=True)
        for i, (x, y, z, count) in enumerate(DECOR_EMPTY_STACKS):
            for level in range(count):
                base = np.array([x, y, z + level * DECOR_TOTE_SIZE[2]])
                self._build_tote(f"/World/DecorTotes/stack_{i}_{level}", base, DECOR_TOTE_SCALE, colliders=False)

    def fill_tote(self, base: np.ndarray, size: np.ndarray, items: list[Item], location_id: str | None) -> None:
        inner = size[:2] / 2 - 0.07
        for order, item in enumerate(self.rng.permutation(len(items))):
            layer = order // TOTE_LAYER
            box = items[item]
            box.location_id = location_id
            box.toppled = bool(self.rng.random() < 0.5) and box.size[0] <= MAX_GRASP_WIDTH
            offset = self.rng.uniform(-inner, inner)
            box.move(base + np.array([offset[0], offset[1], 0.08 + 0.06 * layer]), self.rng.uniform(-math.pi, math.pi))
            tilt = euler_angles_to_quat(np.array([*self.rng.uniform(-0.6, 0.6, 2), 0.0]))
            position, orientation = box.prim.get_world_pose()
            rotation = quat_to_rot_matrix(tilt) @ quat_to_rot_matrix(orientation)
            box.prim.set_world_pose(position, rot_matrix_to_quat(rotation))
            self.set_dynamic(box, True)

    def fill_decor_totes(self) -> None:
        objects = sorted(YCB_SIZES)
        for base in self.decor_totes:
            items = [self.spawn_item(None, None, objects[self.rng.integers(len(objects))]) for _ in range(DECOR_TOTE_ITEMS)]
            self.fill_tote(base, DECOR_TOTE_SIZE, items, None)

    def layout_deep(self, location_id: str, items: list[Item]) -> None:
        location = self.locations[location_id]
        y = float(location["y"])
        front_x = self.face_x(location_id) + BIN_MARGIN
        z = float(location["z"])
        targets = [b for b in items if self.pickable(b.sku)]
        blockers = sorted((b for b in items if not self.pickable(b.sku)), key=lambda b: (abs(b.size[0] - b.size[1]) < 0.003, -b.size[0]))
        for item in items:
            self.set_dynamic(item, False)
            item.toppled = item.flipped = False
        for slot, target, blocker in zip(DEEP_PAIR_SLOTS, targets, blockers):
            blocker.location_id = target.location_id = location_id
            dx, dy = self.rng.uniform(-DEEP_JITTER, DEEP_JITTER, 2)
            yaw = math.pi / 2 + self.rng.uniform(-DEEP_BLOCKER_YAW, DEEP_BLOCKER_YAW)
            depth = float(np.abs(np.array([math.cos(yaw), math.sin(yaw)])) @ blocker.size[:2])
            blocker.rest_on(np.array([front_x + depth / 2 + abs(dx), y + slot + dy, z]), yaw)
            dx, dy = self.rng.uniform(-DEEP_JITTER, DEEP_JITTER, 2)
            target.rest_on(np.array([front_x + blocker.size[1] + DEEP_ROW_GAP + target.size[0] / 2 + dx, y + slot + dy, z]),
                           self.rng.uniform(-CLUSTER_YAW, CLUSTER_YAW))
        for slot, extra in zip(DEEP_EXTRA_SLOTS, blockers[len(targets):] + targets[len(blockers):]):
            extra.location_id = location_id
            extra.rest_on(np.array([front_x + extra.size[0] / 2, y + slot, z]), self.rng.uniform(-CLUSTER_YAW, CLUSTER_YAW))
        for item in items:
            self.set_dynamic(item, True)

    def layout_bin(self, location_id: str, groups: list[list[Item]]) -> None:
        location = self.locations[location_id]
        x, y, z = float(location["x"]), float(location["y"]), float(location["z"])
        front_x = x - SHELF_DEPTH / 2 + BIN_MARGIN
        depth = SHELF_DEPTH - 2 * BIN_MARGIN
        usable = BIN_LENGTH - 0.2
        clusters = []
        for group in groups:
            pickable = self.pickable(group[0].sku)
            if pickable and any(b.toppled for b in group):
                extent = np.full(2, 2 * max(b.radius for b in group))
            else:
                angle = YAW_SPREAD_PICKABLE if pickable else YAW_SPREAD_OTHER
                rotation = np.abs(np.array([[math.cos(angle), math.sin(angle)], [math.sin(angle), math.cos(angle)]]))
                extent = np.max([np.maximum(b.footprint[:2], rotation @ b.footprint[:2]) for b in group], axis=0)
            cell = extent + np.array([CLUSTER_GAP, PICK_GAP if pickable else CLUSTER_GAP])
            max_rows = max(1, int(depth // cell[0]))
            rows = 1 if pickable else min(max_rows, 1 if len(group) <= 3 else 2)
            columns = math.ceil(len(group) / rows)
            while columns * cell[1] > usable and rows < max_rows:
                rows += 1
                columns = math.ceil(len(group) / rows)
            capacity = rows * max(1, int(usable // cell[1]))
            for item in group[capacity:]:
                self.stage.RemovePrim(item.path)
                self.boxes.remove(item)
            group = group[:capacity]
            columns = math.ceil(len(group) / rows)
            clusters.append((group, cell, columns))
        while len(clusters) > 1 and sum(c[2] * c[1][1] for c in clusters) > usable:
            dropped = next((c for c in reversed(clusters) if c[0][0].sku is None), clusters[-1])
            clusters.remove(dropped)
            for item in dropped[0]:
                self.stage.RemovePrim(item.path)
                self.boxes.remove(item)
        order = self.rng.permutation(len(clusters))
        slack = max(0.0, usable - sum(c[2] * c[1][1] for c in clusters))
        gaps = self.rng.dirichlet(np.ones(len(clusters) + 1)) * slack
        cursor = y - usable / 2
        for gap, index in zip(gaps, order):
            group, cell, columns = clusters[index]
            pickable = self.pickable(group[0].sku)
            cursor += gap
            if pickable:
                cursor += PICK_GAP / 2
            for k, item in enumerate(group):
                row, column = divmod(k, columns)
                jitter = self.rng.uniform(-CLUSTER_JITTER, CLUSTER_JITTER, 2)
                surface = np.array([front_x + cell[0] * (row + 0.5) + jitter[0],
                                    cursor + cell[1] * (column + 0.5) + jitter[1], z])
                item.location_id = location_id
                if item.toppled and pickable:
                    spread = math.pi / 2
                else:
                    spread = YAW_SPREAD_PICKABLE if pickable else YAW_SPREAD_OTHER
                item.rest_on(surface, self.rng.uniform(-spread, spread))
                self.set_dynamic(item, True)
            cursor += columns * cell[1]

    def cluster_spot(self, location_id: str, box: Item) -> tuple[np.ndarray, float]:
        location = self.locations[location_id]
        x, y, z = float(location["x"]), float(location["y"]), float(location["z"])
        half = (BIN_LENGTH - 0.2) / 2
        front_x = x - SHELF_DEPTH / 2 + BIN_MARGIN + box.footprint[0] / 2 + CLUSTER_GAP / 2
        gap = PICK_GAP if self.pickable(box.sku) else CLUSTER_GAP
        width = box.footprint[1] + gap
        others = [b for b in self.boxes_at(location_id, background=True) if b is not box]
        same = sorted((b.position[1] for b in others if b.sku == box.sku and abs(b.position[0] - front_x) < 0.05))
        if same:
            candidates = [same[-1] + width, same[0] - width]
        else:
            candidates = list(np.arange(y - half + width / 2, y + half - width / 2 + 1e-6, 0.01))
        for candidate_y in candidates:
            point = np.array([front_x, candidate_y, z])
            inside = y - half + width / 2 - 1e-6 <= candidate_y <= y + half - width / 2 + 1e-6
            if inside and not any(np.all(np.abs(point[:2] - o.position[:2]) < (box.footprint[:2] + o.footprint[:2]) / 2
                                         + np.array([0.0, gap])) for o in others):
                jitter = self.rng.uniform(-CLUSTER_JITTER, CLUSTER_JITTER, 2)
                return point + np.append(jitter, 0.0), self.rng.uniform(-CLUSTER_YAW, CLUSTER_YAW)
        return self.free_spot(location_id, box, front=True)

    def shelve(self, box: Item, location_id: str) -> None:
        surface, yaw = self.cluster_spot(location_id, box)
        box.location_id = location_id
        box.rest_on(surface, yaw)
        self.set_dynamic(box, True)

    def set_dynamic(self, box: Item, dynamic: bool) -> None:
        body = UsdPhysics.RigidBodyAPI(self.stage.GetPrimAtPath(box.path))
        body.GetRigidBodyEnabledAttr().Set(dynamic)
        body.GetKinematicEnabledAttr().Set(False)
        for prim in Usd.PrimRange(self.stage.GetPrimAtPath(box.path)):
            if prim.HasAPI(UsdPhysics.CollisionAPI):
                UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Set(dynamic)

    def sync(self, box: Item) -> np.ndarray:
        position, orientation = box.prim.get_world_pose()
        box.position = np.array(position)
        return box.position

    def holding(self, box: Item, start_z: float) -> bool:
        position = self.sync(box)
        return position[2] - start_z > 0.015 and float(np.linalg.norm(self.fingertip() - position)) < HOLD_TOLERANCE

    def boxes_at(self, location_id: str, background: bool = False) -> list[Item]:
        return [b for b in self.boxes if b.location_id == location_id and (background or b.sku)]

    def pickable(self, sku: str | None) -> bool:
        return bool(sku) and bool(self.products[sku]["robot_pickable"])

    def repack(self, location_id: str) -> None:
        if self.locations[location_id]["kind"] == "storage" or (location_id == TOTE_BIN and args.scenario == "hard"):
            return
        boxes = sorted(self.boxes_at(location_id), key=lambda b: (b.sku, b.path))
        for box, surface in zip(boxes, self.slot_surfaces(location_id, len(boxes))):
            box.rest_on(surface, 0.0)

    def reset_inventory(self, missing: list[dict], seed: int = 0, misplaced: list[dict] | None = None) -> dict:
        self.rng = np.random.default_rng(seed)
        for box in self.boxes:
            self.stage.RemovePrim(box.path)
        for path, _ in self.shipping:
            self.stage.RemovePrim(path)
        self.shipping = []
        self.reset_dispatch_totes()
        self.boxes, self.tote, self.held, self.tool, self.trail = [], [], None, None, None
        misplaced = misplaced or []
        skip = {(m["location_id"], m["sku"]) for m in missing} | {(m["from"], m["sku"]) for m in misplaced}
        by_location: dict[str, list[tuple[str, int]]] = {}
        for row in api_get("/api/inventory/locations"):
            if (row["location_id"], row["sku"]) not in skip and row["quantity"] > 0:
                by_location.setdefault(row["location_id"], []).append((row["sku"], row["quantity"]))
        for m in misplaced:
            by_location.setdefault(m["to"], []).append((m["sku"], m["quantity"]))
        for location_id, items in by_location.items():
            if self.locations[location_id]["kind"] == "storage":
                continue
            skus = [sku for sku, qty in items for _ in range(qty)]
            if location_id == TOTE_BIN and args.scenario == "hard":
                tote_items = [self.spawn_item(sku, location_id) for sku in skus]
                tote_items += [self.spawn_item(None, location_id, ycb) for ycb in TOTE_CLUTTER]
                self.fill_tote(self.receiving_tote, RECEIVING_TOTE_SIZE, tote_items, location_id)
                continue
            for sku, surface in zip(skus, self.slot_surfaces(location_id, len(skus))):
                item = self.spawn_item(sku, location_id)
                item.rest_on(surface, 0.0)
                self.set_dynamic(item, self.locations[location_id]["kind"] == "staging")
        if args.scenario == "hard":
            self.fill_decor_totes()
        for location in self.locations.values():
            if location["kind"] != "storage":
                continue
            groups = [[self.spawn_item(sku, None) for _ in range(qty)] for sku, qty in by_location.get(location["id"], [])]
            if args.scenario == "hard" and location["id"] == DEEP_BIN and groups:
                self.layout_deep(location["id"], [item for group in groups for item in group])
                continue
            for group in groups:
                for item in group:
                    if self.products[item.sku]["robot_pickable"]:
                        rate = 0.0 if args.scenario == "nominal" else 1.0 if location["id"] == TOPPLE_BIN else PICK_TOPPLE_RATE
                        if self.rng.random() < rate:
                            if item.size[0] <= MAX_GRASP_WIDTH:
                                item.toppled = True
                    else:
                        item.toppled = len(group) <= TOPPLE_MAX_CLUSTER and bool(self.rng.random() < TOPPLE_RATE)
            if groups:
                self.layout_bin(location["id"], groups)
        return self.counts()

    def counts(self) -> dict:
        result: dict[str, dict[str, int]] = {}
        for box in self.boxes:
            if box.location_id and box.sku:
                result.setdefault(box.location_id, {}).setdefault(box.sku, 0)
                result[box.location_id][box.sku] += 1
        return result

    def approach_pose(self, location_id: str) -> np.ndarray:
        location = self.locations[location_id]
        x, y = float(location["x"]), float(location["y"])
        if location["kind"] not in STANCE_CLEARANCE:
            return np.array([x, y, 0.0])
        reach = DISPATCH_REACH if location["kind"] == "dispatch" else WORK_REACH
        stance_x = self.face_x(location_id) - ARM_MOUNT - reach
        return np.array([stance_x, y, WORK_HEADING])

    def face_x(self, location_id: str) -> float:
        location = self.locations[location_id]
        x = float(location["x"])
        if location["kind"] == "storage":
            return x - SHELF_DEPTH / 2
        if location["kind"] == "staging":
            return x + 0.05
        return CONVEYOR_FACE_X

    def solve_ik(self, position: np.ndarray, orientation: np.ndarray = FRONT_GRASP, seed: np.ndarray | None = None) -> np.ndarray:
        base_position, base_orientation = self.link0.get_world_pose()
        self.ik.set_robot_base_pose(base_position, base_orientation)
        position = np.asarray(position, dtype=float) - quat_to_rot_matrix(orientation) @ self.tool_tip()
        seed = self.arm_target.copy() if seed is None else np.asarray(seed, dtype=float)
        solutions = []
        warm_starts = [seed] + [seed + np.random.normal(0.0, 0.15, size=seed.shape) for _ in range(IK_ATTEMPTS - 1)]
        for batch in (warm_starts, IK_REACH_SEEDS):
            for warm_start in batch:
                joints, success = self.ik.compute_inverse_kinematics(
                    "right_gripper", position, orientation, warm_start=np.asarray(warm_start, dtype=float))
                if success:
                    solutions.append(np.array(joints))
            if solutions and min(float(np.abs(j - seed).max()) for j in solutions) < IK_NEAR:
                break
        if not solutions:
            raise SimError("ik_failed", f"no arm solution for target {np.round(position, 2).tolist()}")
        return min(solutions, key=lambda joints: float(np.linalg.norm(joints - seed)))


def instruction_for(warehouse: "Warehouse", sku: str) -> str:
    return f"pick up the {warehouse.products[sku]['name'].lower()}"


def wrap_angle(angle: float) -> float:
    return (angle + math.pi) % (2 * math.pi) - math.pi


class Commands:
    def __init__(self, warehouse: Warehouse, pose_source: str | None = None):
        self.w = warehouse
        self.recording = False
        self.base_carry = True
        self.pose_source = pose_source or args.pose
        self.target: Target | None = None
        self.grasp_choice: dict | None = None
        self.last_grasp: dict = {}

    def true_target(self, box: Item) -> Target:
        return self.w.true_target(box)

    def locate(self, box: Item):
        truth = self.true_target(box)
        if self.pose_source == "sim":
            return truth
        surface_z = self.w.surface_z(box.location_id)
        upper = surface_z > UPPER_LEVEL_Z
        self.w.look_z = surface_z if upper else surface_z + HEAD_LOOK_ABOVE
        self.w.look_reach = UPPER_HEAD_REACH if upper else HEAD_REACH
        self.w.look_height = UPPER_HEAD_HEIGHT if upper else HEAD_HEIGHT
        if upper and np.abs(self.w.arm_target - self.w.arm_home).max() > 0.05:
            yield from self.move_arm(self.w.arm_home, 60)
        for _ in range(6):
            yield
        estimate = self.w.estimate_target(box, surface_z)
        self.last_grasp = {**self.last_grasp, "pose_error_m": round(float(np.linalg.norm(estimate.center - truth.center)), 3),
                           "height_error_m": round(estimate.height - truth.height, 3)}
        return estimate

    def drive_to(self, target: np.ndarray, speed: float = BASE_SPEED):
        velocity, turn_rate = 0.0, 0.0
        while True:
            current = self.w.base_target.copy()
            delta = target[:2] - current[:2]
            distance = float(np.linalg.norm(delta))
            heading_error = wrap_angle(target[2] - current[2])
            if distance < 1e-3 and abs(heading_error) < 1e-3:
                break
            braking = math.sqrt(2 * BASE_ACCEL * distance)
            velocity = min(speed, velocity + BASE_ACCEL * PHYSICS_DT, braking)
            if distance > 0:
                current[:2] += delta / distance * min(distance, max(velocity, 0.02) * PHYSICS_DT)
            turn_braking = math.sqrt(2 * BASE_TURN_ACCEL * abs(heading_error))
            turn_rate = min(BASE_TURN_SPEED, turn_rate + BASE_TURN_ACCEL * PHYSICS_DT, turn_braking)
            current[2] += math.copysign(min(abs(heading_error), max(turn_rate, 0.02) * PHYSICS_DT), heading_error)
            self.w.base_target = current
            yield
        for _ in range(20):
            yield

    def aisle_center(self, x: float) -> float:
        racks = sorted({float(l["x"]) for l in self.w.locations.values() if l["kind"] == "storage"})
        left = max((r + SHELF_DEPTH / 2 for r in racks if r + SHELF_DEPTH / 2 <= x), default=None)
        right = min((r - SHELF_DEPTH / 2 for r in racks if r - SHELF_DEPTH / 2 >= x), default=None)
        if left is None or right is None:
            return x
        return (left + right) / 2

    def waypoints(self, goal: np.ndarray) -> list[np.ndarray]:
        x, y, _ = self.w.base_target
        lane = self.aisle_center(goal[0])
        points = []
        if abs(y - CORRIDOR_Y) > 0.05 and abs(x - lane) > 0.05:
            points.append(np.array([self.aisle_center(x), y, 0.0]))
            points.append(np.array([self.aisle_center(x), CORRIDOR_Y, 0.0]))
        if abs(x - lane) > 0.05:
            points.append(np.array([lane, CORRIDOR_Y, 0.0]))
        points.append(np.array([lane, goal[1], 0.0]))
        return points

    def back_off(self, pose: np.ndarray, distance: float) -> np.ndarray:
        forward = np.array([math.cos(pose[2]), math.sin(pose[2]), 0.0])
        return pose - forward * distance

    def navigate(self, location_id: str):
        goal = self.w.approach_pose(location_id)
        yield from self.move_arm(self.w.arm_home, 40)
        current = self.w.base_target.copy()
        storage = self.w.locations.get(location_id, {}).get("kind") == "storage"
        if storage:
            goal = self.back_off(goal, SCAN_STANDOFF)
        facing_racks = abs(wrap_angle(current[2] - WORK_HEADING)) < 0.1
        if storage and facing_racks and abs(self.aisle_center(current[0]) - self.aisle_center(goal[0])) < 0.05:
            yield from self.drive_to(goal)
            x, y, theta = self.w.base_pose()
            return {"x": float(x), "y": float(y), "theta": float(theta)}
        if facing_racks:
            yield from self.drive_to(self.back_off(current, UNDOCK))
        docked = not storage and abs(wrap_angle(goal[2] - WORK_HEADING)) < 1e-3
        final = goal
        if docked:
            goal = self.back_off(goal, UNDOCK)
        for point in self.waypoints(goal):
            heading = math.atan2(point[1] - self.w.base_target[1], point[0] - self.w.base_target[0])
            if np.linalg.norm(point[:2] - self.w.base_target[:2]) > 0.05:
                yield from self.drive_to(np.array([self.w.base_target[0], self.w.base_target[1], heading]))
                yield from self.drive_to(np.array([point[0], point[1], heading]))
        yield from self.drive_to(goal)
        if docked:
            yield from self.drive_to(final)
        x, y, theta = self.w.base_pose()
        return {"x": float(x), "y": float(y), "theta": float(theta)}

    def move_arm(self, target: np.ndarray, steps: int = 90):
        steps = max(8, int(steps / MOTION_SPEEDUP))
        start = self.w.arm_target.copy()
        for i in range(1, steps + 1):
            t = 0.5 - 0.5 * math.cos(math.pi * i / steps)
            self.w.arm_target = start + (target - start) * t
            yield
        yield from self.settle_arm()

    def settle_arm(self, tolerance: float = 0.03, limit: int = 120):
        for _ in range(limit):
            error = np.abs(self.w.robot.get_joint_positions()[self.w.arm_idx] - self.w.arm_target).max()
            if error < tolerance:
                break
            yield

    def gripper(self, opening: float):
        self.w.finger_target = opening
        for _ in range(25):
            yield

    def reach(self, position: np.ndarray, steps: int = 90, orientation: np.ndarray = FRONT_GRASP):
        joints = self.w.solve_ik(position, orientation)
        yield from self.move_arm(joints, steps)

    def reach_linear(self, position: np.ndarray, orientation: np.ndarray, speed: float = 0.25):
        speed *= MOTION_SPEEDUP
        start = self.w.fingertip()
        distance = float(np.linalg.norm(position - start))
        segments = max(1, int(distance / LINEAR_STEP))
        ticks = max(1, int(distance / segments / speed / PHYSICS_DT))
        plan, previous, smooth = [], self.w.arm_target.copy(), True
        for i in range(1, segments + 1):
            point = start + (position - start) * i / segments
            try:
                joints = self.w.solve_ik(point, orientation, previous)
            except SimError:
                if i == segments:
                    raise
                continue
            if float(np.abs(joints - previous).max()) > LINEAR_MAX_JOINT_STEP:
                smooth = False
            plan.append(joints)
            previous = joints
        if not smooth:
            yield from self.move_arm(plan[-1], max(20, int(distance / speed / PHYSICS_DT) * 2))
            return
        for goal in plan:
            begin = self.w.arm_target.copy()
            for k in range(1, ticks + 1):
                self.w.arm_target = begin + (goal - begin) * k / ticks
                yield
        yield from self.settle_arm()

    def reach_blend(self, position: np.ndarray, orientation: np.ndarray, speed: float = 0.3):
        speed *= MOTION_SPEEDUP
        start = self.w.fingertip()
        start_orientation = np.asarray(self.w.hand.get_world_pose()[1], dtype=float)
        end_orientation = np.asarray(orientation, dtype=float)
        if start_orientation @ end_orientation < 0:
            end_orientation = -end_orientation
        segments = max(4, int(float(np.linalg.norm(position - start)) / LINEAR_STEP))
        ticks = max(1, int(float(np.linalg.norm(position - start)) / segments / speed / PHYSICS_DT))
        for i in range(1, segments + 1):
            t = i / segments
            blended = start_orientation * (1 - t) + end_orientation * t
            try:
                goal = self.w.solve_ik(start + (position - start) * t, blended / np.linalg.norm(blended))
            except SimError:
                if i == segments:
                    raise
                continue
            begin = self.w.arm_target.copy()
            for k in range(1, ticks + 1):
                self.w.arm_target = begin + (goal - begin) * k / ticks
                yield

    def smooth_approach(self, option: dict, max_step: float = PUSH_MAX_JOINT_STEP) -> tuple[np.ndarray, list[np.ndarray]] | None:
        above = option["approach"] + np.array([0.0, 0.0, GRASP_READY_LIFT])
        natural = self.natural_orientation(option["approach"], option["orientation"])
        flipped = rot_matrix_to_quat(quat_to_rot_matrix(natural) @ np.diag([-1.0, -1.0, 1.0]))
        for orientation in (natural, flipped):
            try:
                plan = self.plan_line(above, option["approach"], orientation, max_step)
            except SimError:
                continue
            option["orientation"] = orientation
            return above, plan
        return None

    def go_to_pregrasp(self, joints: np.ndarray):
        above, plan = self.grasp_choice["plan"]
        self.last_plan_goal = plan[-1]
        yield from self.move_arm(plan[0], 90)
        self.check_tracking(above, "grasp ready")
        yield from self.follow(plan, GRASP_READY_LIFT, 0.15)

    def reach_first(self, targets: list[np.ndarray], orientation: np.ndarray, speed: float):
        for target in targets:
            try:
                self.w.solve_ik(target, orientation)
            except SimError:
                continue
            yield from self.reach_linear(target, orientation, speed)
            return

    def reach_any(self, position: np.ndarray, steps: int, orientations: list[np.ndarray]):
        for orientation in orientations:
            try:
                joints = self.w.solve_ik(position, orientation)
            except SimError:
                continue
            yield from self.move_arm(joints, steps)
            return
        raise SimError("ik_failed", f"no arm solution for target {np.round(position, 2).tolist()}")

    def inspect(self, location_id: str, distance: float = 1.3, height: float = 0.25, focal: float = 16.0):
        location = self.w.locations[location_id]
        x, y, z = float(location["x"]), float(location["y"]), float(location["z"])
        if location["kind"] == "staging":
            x, z = x + 0.12, STAGING_HEIGHT
        approach = self.w.approach_pose(location_id)
        tote = location["kind"] == "dispatch"
        at_bin = not tote and np.linalg.norm(self.w.base_target[:2] - approach[:2]) < 0.3
        if at_bin:
            yield from self.drive_to(approach - np.array([SCAN_STANDOFF, 0.0, 0.0]))
        UsdGeom.Camera(self.w.stage.GetPrimAtPath("/World/InspectCam")).GetFocalLengthAttr().Set(focal)
        if tote:
            UsdGeom.Camera(self.w.stage.GetPrimAtPath("/World/InspectCam")).GetFocalLengthAttr().Set(TOTE_AUDIT_FOCAL)
            set_camera_view(eye=np.array([TOTE_X - 0.92, y, ROLLER_TOP + 1.34]), target=np.array([TOTE_X, y, ROLLER_TOP + 0.03]),
                            camera_prim_path="/World/InspectCam")
        else:
            eye, target = np.array([x - distance, y, z + height]), np.array([x, y, z + 0.06])
            set_camera_view(eye=eye, target=target, camera_prim_path="/World/InspectCam")
            self.w.views[location_id] = (eye, target, focal)
        for _ in range(8):
            yield
        frame = self.w.annotators["inspect"].get_data()
        buffer = io.BytesIO()
        Image.fromarray(frame[..., :3]).save(buffer, format="JPEG", quality=85)
        if at_bin:
            yield from self.drive_to(approach)
        counts = {}
        for box in self.w.boxes_at(location_id):
            counts[box.sku] = counts.get(box.sku, 0) + 1
        return {"location_id": location_id, "counts": counts, "image_jpeg_b64": base64.b64encode(buffer.getvalue()).decode()}

    def project(self, location_id: str, point: np.ndarray) -> np.ndarray:
        eye, target, focal = self.w.views[location_id]
        forward = (target - eye) / np.linalg.norm(target - eye)
        right = np.cross(forward, np.array([0.0, 0.0, 1.0]))
        right /= np.linalg.norm(right)
        up = np.cross(right, forward)
        relative = point - eye
        depth = float(relative @ forward)
        pixels_per_unit = INSPECT_RES[0] * focal / INSPECT_APERTURE
        return np.array([INSPECT_RES[0] / 2 + pixels_per_unit * float(relative @ right) / depth,
                         INSPECT_RES[1] / 2 - pixels_per_unit * float(relative @ up) / depth])

    def grounded_box(self, location_id: str, point: list[float]) -> Item:
        if location_id not in self.w.views:
            raise SimError("not_inspected", f"{location_id} has no inspection image to ground against")
        pixel = np.array(point, dtype=float) / 1000.0 * np.array(INSPECT_RES)
        items = self.w.boxes_at(location_id, background=True)
        if not items:
            raise SimError("item_not_found", f"nothing at {location_id}")
        return min(items, key=lambda b: float(np.linalg.norm(self.project(location_id, self.w.sync(b)) - pixel)))

    def resolve(self, location_id: str, point: list[float]):
        box = self.grounded_box(location_id, point)
        yield
        return {"sku": box.sku, "object": box.ycb,
                "pixel": (self.project(location_id, box.position) / np.array(INSPECT_RES) * 1000).round().tolist()}

    def target_box(self, sku: str, location_id: str) -> Item:
        candidates = [b for b in self.w.boxes_at(location_id) if b.sku == sku]
        if not candidates:
            raise SimError("item_not_found", f"no {sku} at {location_id}")
        base = self.w.base_pose()
        return min(candidates, key=lambda b: np.linalg.norm(b.prim.get_world_pose()[0][:2] - base[:2]))

    def work_yaw(self) -> float:
        return float(self.w.base_target[2])

    def grasp_options(self, target: Target, surface_z: float, top_down: bool = False) -> list[dict]:
        work = self.work_yaw()
        yaws = [work + offset for offset in (0.0, 0.35, -0.35, 0.7, -0.7)]
        for axis in target.axes.T:
            if np.linalg.norm(axis[:2]) > 0.3:
                for angle in (math.atan2(axis[1], axis[0]), math.atan2(-axis[1], -axis[0])):
                    if top_down or abs(wrap_angle(angle - work)) < 1.1:
                        yaws.append(angle)
                        yaws.append(angle + math.pi / 2)
        height = target.height
        top = target.center[2] + height / 2
        options = []
        for yaw in yaws:
            for pitch in ((1.2, math.pi / 2) if top_down else (*GRASP_PITCH_FALLBACKS, 1.2, math.pi / 2)):
                for roll in (0.0, math.pi / 2):
                    rotation = tool_rotation(yaw, pitch, roll)
                    width = target.grasp_width(rotation[:, 1], rotation[:, 2])
                    if width > MAX_GRASP_WIDTH:
                        continue
                    if pitch > 1.0:
                        drop = min(0.03, height / 2)
                    else:
                        drop = min(max(GRASP_BELOW_TOP, height * 0.3), height - 0.02)
                    position = np.array([target.center[0], target.center[1], top - drop])
                    if position[2] - abs(rotation[2, 1]) * FINGER_REACH < surface_z + 0.005:
                        continue
                    preferred = SHORT_GRASP_PITCH if height < SHORT_ITEM else GRASP_PITCH
                    score = (abs(wrap_angle(yaw - work)) + abs(pitch - preferred) * (0.5 if top_down else STEEP_WEIGHT)
                             + (roll % math.pi > 0) * 0.2 + width * WIDTH_WEIGHT)
                    options.append({"yaw": yaw, "pitch": pitch, "roll": roll, "position": position,
                                    "direction": rotation[:, 2], "orientation": rot_matrix_to_quat(rotation), "score": score})
        return sorted(options, key=lambda o: o["score"])

    def carry_orientation(self, yaw: float) -> np.ndarray:
        choice = self.grasp_choice or {"pitch": GRASP_PITCH, "roll": 0.0}
        return rot_matrix_to_quat(tool_rotation(yaw, choice["pitch"], choice["roll"]))

    def face_work(self):
        if np.abs(self.w.arm_target - self.w.work_home).max() > 0.05:
            yield from self.move_arm(self.w.work_home, 60)

    def align_to(self, point: np.ndarray):
        _, _, theta = self.w.base_target
        left = np.array([-math.sin(theta), math.cos(theta)])
        lateral = float(self.w.to_base_frame(point)[1])
        target = self.w.base_target.copy()
        target[:2] += left * lateral
        yield from self.drive_to(target)

    def prepare(self, box: Item):
        self.grasp_choice = None
        yield from self.face_work()
        coarse = yield from self.locate(box)
        yield from self.align_to(coarse.center)
        self.target = yield from self.locate(box)
        pregrasp_joints = self.choose_grasp(box)
        yield from self.gripper(0.04)
        yield from self.go_to_pregrasp(pregrasp_joints)

    def choose_grasp(self, box: Item) -> np.ndarray:
        self.grasp_choice = None
        pregrasp_joints = None
        reachable = []
        top_down = box.location_id == TOTE_BIN and args.scenario == "hard"
        for option in self.grasp_options(self.target, self.w.surface_z(box.location_id), top_down):
            try:
                pregrasp_joints = self.w.solve_ik(option["position"] - option["direction"] * GRASP_STANDOFF, option["orientation"])
                self.w.solve_ik(option["position"], option["orientation"])
            except SimError:
                continue
            option["approach"] = option["position"] - option["direction"] * GRASP_STANDOFF
            try:
                approach = option["position"] - option["direction"] * (GRASP_STANDOFF + APPROACH_BACKOFF)
                pregrasp_joints = self.w.solve_ik(approach, option["orientation"])
                option["approach"] = approach
            except SimError:
                pass
            plan = self.smooth_approach(option)
            if plan is None:
                reachable.append(option)
                continue
            option["plan"] = plan
            self.grasp_choice = option
            break
        for option in reachable if self.grasp_choice is None else []:
            plan = self.smooth_approach(option, RELAXED_JOINT_STEP)
            if plan is not None:
                option["plan"] = plan
                self.grasp_choice = option
                break
        self.last_grasp = {**self.last_grasp, "target_upright": round(float(np.abs(self.true_target(box).axes[2]).max()), 2),
                           "options": len(self.grasp_options(self.target, self.w.surface_z(box.location_id), top_down))}
        if self.last_grasp["options"] == 0:
            self.last_grasp = {**self.last_grasp, "extents": np.round(self.target.extents, 3).tolist(),
                               "axes": np.round(self.target.axes, 2).tolist(), "work_yaw": round(self.work_yaw(), 2)}
        if self.grasp_choice is None:
            raise SimError("ik_failed", f"no reachable grasp for {box.sku} at {box.location_id}")
        self.grasp_roll = self.grasp_choice["roll"]
        self.last_grasp = {**self.last_grasp, **{k: round(float(self.grasp_choice[k]), 3) for k in ("yaw", "pitch", "roll")}}
        return pregrasp_joints

    def expert_episode(self, box: Item, perturbation: str | None, rng: np.random.Generator):
        self.last_grasp = {}
        if self.w.locations[box.location_id]["kind"] == "storage":
            yield from self.clear_blockers(box)
        self.target = self.true_target(box)
        if perturbation == "offset":
            lateral = rng.uniform(*OFFSET_RANGE) * rng.choice([-1.0, 1.0])
            self.target.center = self.target.center + np.array([0.0, lateral, 0.0])
        yield from self.gripper(0.04)
        yield from self.go_to_pregrasp(self.choose_grasp(box))
        if perturbation == "nudge":
            start = self.w.sync(box).copy()
            shift = np.append(rng.uniform(-1, 1, 2), 0.0)
            shift *= rng.uniform(*NUDGE_RANGE) / max(float(np.linalg.norm(shift)), 1e-6)
            position, orientation = box.prim.get_world_pose()
            for i in range(1, NUDGE_TICKS + 1):
                box.prim.set_world_pose(start + shift * i / NUDGE_TICKS, orientation)
                yield
        if perturbation in ("offset", "nudge"):
            self.target = self.true_target(box)
            yield from self.go_to_pregrasp(self.choose_grasp(box))
        if perturbation == "regrasp":
            choice = dict(self.grasp_choice)
            miss = rng.uniform(*OFFSET_RANGE) * rng.choice([-1.0, 1.0])
            rotation = quat_to_rot_matrix(choice["orientation"])
            self.grasp_choice = {**choice, "position": choice["position"] + rotation[:, 1] * miss}
            yield from self.arm_grasp(box)
            yield from self.gripper(0.04)
            yield from self.reach_linear(self.grasp_choice["position"] - choice["direction"] * GRASP_STANDOFF,
                                         choice["orientation"], 0.2)
            self.target = self.true_target(box)
            yield from self.go_to_pregrasp(self.choose_grasp(box))
        self.last_grasp = {**self.last_grasp, "perturbation": perturbation}
        yield from self.arm_grasp(box)

    def blockers(self, box: Item) -> list[Item]:
        work = np.array([math.cos(self.work_yaw()), math.sin(self.work_yaw()), 0.0])
        lateral = np.array([-work[1], work[0], 0.0])
        target = self.true_target(box)
        width = target.extent_along(lateral)
        found = []
        for other in self.w.boxes_at(box.location_id, background=True):
            if other is box:
                continue
            obb = self.true_target(other)
            in_front = (target.center - obb.center) @ work > 0.03
            overlap = abs((obb.center - target.center) @ lateral) < (width + obb.extent_along(lateral)) / 2
            if in_front and overlap:
                found.append(other)
        return found

    def push_aside(self, blocker: Item, target_box: Item, policy: str = "expert"):
        work = np.array([math.cos(self.work_yaw()), math.sin(self.work_yaw()), 0.0])
        lateral = np.array([-work[1], work[0], 0.0])
        obb = self.true_target(blocker)
        half = obb.extent_along(lateral) / 2
        row = [self.true_target(o) for o in self.w.boxes_at(blocker.location_id, background=True)
               if o is not blocker and o is not target_box and abs((self.true_target(o).center - obb.center) @ work) < 0.08]
        location = self.w.locations[blocker.location_id]
        bin_half = BIN_LENGTH / 2 - 0.05
        free = {}
        for side in (1.0, -1.0):
            edge = float(location["y"]) * lateral[1] + float(location["x"]) * lateral[0] + side * bin_half
            limit = abs(edge - obb.center @ lateral) - half
            for other in row:
                gap = side * ((other.center - obb.center) @ lateral) - half - other.extent_along(lateral) / 2
                if gap > -0.01:
                    limit = min(limit, gap)
            free[side] = limit
        bin_center = np.array([float(location["x"]), float(location["y"]), 0.0])
        side = 1.0 if (obb.center - self.true_target(target_box).center) @ lateral >= 0 else -1.0
        if abs(float((obb.center - bin_center) @ lateral)) > 0.05:
            side = 1.0 if (obb.center - bin_center) @ lateral >= 0 else -1.0
        target = self.true_target(target_box)
        travel = target.extent_along(lateral) + half + PUSH_GOAL_CLEARANCE
        origin = obb.center.copy()
        up_axis = int(np.argmax(np.abs(obb.axes[2, :])))
        distance = travel + PUSH_CLEARANCE + PUSH_OVERSHOOT
        if free[side] < distance <= free[-side]:
            side = -side
        goal_lateral = side * travel
        if free[side] < distance:
            raise SimError("blocked", f"no room to push {blocker.ycb} aside at {blocker.location_id}")
        distance = min(free[side], distance + PUSH_SLIP_MARGIN)
        yield from self.face_work()
        yield from self.align_to(obb.center + side * lateral * travel / 2)
        blade = TOOL_PARTS["paddle"][-1][1][1] / 2
        start = obb.center - side * lateral * (half + PUSH_CLEARANCE + blade)
        orientation = rot_matrix_to_quat(tool_rotation(self.work_yaw(), 0.0, 0.0))
        approach = None
        for height in (PUSH_HEIGHT, PUSH_HEIGHT + 0.02):
            start[2] = self.w.surface_z(blocker.location_id) + height
            try:
                self.w.solve_ik(start - work * (PUSH_STANDOFF + PUSH_READY) + np.array([0.0, 0.0, PUSH_READY_LIFT]), orientation)
                approach = self.w.solve_ik(start - work * (PUSH_STANDOFF + PUSH_ENTRY), orientation)
                self.w.solve_ik(start + side * lateral * distance, orientation)
            except SimError:
                approach = None
                continue
            break
        if approach is None:
            raise SimError("ik_failed", f"no arm solution to push {blocker.ycb} at {blocker.location_id}: start "
                                        f"{np.round(start, 2).tolist()} side {side} base {np.round(self.w.base_pose(), 2).tolist()}")
        self.w.trail = []
        self.recording = True
        if policy in ("vla", "teleop"):
            instruction = f"push the {BLOCKER_NAMES[blocker.ycb]} to the {'left' if side > 0 else 'right'}"
            if policy == "teleop":
                TELEOP.update(instruction=instruction, goal=round(travel, 3))
                ready_point = start - work * (PUSH_STANDOFF + PUSH_READY) + np.array([0.0, 0.0, PUSH_READY_LIFT])
                ready_orientation = self.natural_orientation(start - work * PUSH_STANDOFF, orientation)
                yield from self.move_arm(self.w.solve_ik(ready_point, ready_orientation, self.reach_seed()), 90)
                for _ in range(20):
                    yield
                yield from self.push_teleop(instruction, blocker, origin, lateral, side)
            else:
                yield from self.push_vla(instruction, blocker, origin, lateral, side * travel)
            self.push_trace = {"side": side, "instruction": instruction, "goal_lateral": round(travel, 3), "vla_debug": getattr(self, "vla_debug", None),
                               "end_tip": np.round(self.w.to_base_frame(self.w.fingertip()), 3).tolist(),
                               "final_lateral": round(float((self.w.sync(blocker) - origin) @ lateral) * side, 3),
                               "final_depth": round(float((self.w.sync(blocker) - origin) @ work), 3),
                               "upright": bool(abs(float(self.true_target(blocker).axes[2, up_axis])) > UPRIGHT_COS)}
            self.w.trail = None
            self.recording = False
            return
        ready_point = start - work * (PUSH_STANDOFF + PUSH_READY) + np.array([0.0, 0.0, PUSH_READY_LIFT])
        self.push_joints = None
        orientation = self.natural_orientation(start - work * PUSH_STANDOFF, orientation)
        yield from self.approach_along(ready_point, start - work * PUSH_STANDOFF, orientation, 0.15, "push ready")
        self.push_joints = self.last_plan_goal.copy()
        entry_tip = np.round(self.w.fingertip(), 3).tolist()
        entry_joint_error = round(float(np.abs(self.w.robot.get_joint_positions()[self.w.arm_idx] - self.w.arm_target).max()), 3)
        trace = {"entry_cmd": np.round(start - work * (PUSH_STANDOFF + PUSH_ENTRY), 3).tolist(), "entry_tip": entry_tip,
                 "entry_joint_error": entry_joint_error, "blocker": np.round(obb.center, 3).tolist(), "side": side, "distance": round(distance, 3),
                 "start_cmd": np.round(start, 3).tolist(), "standoff_tip": np.round(self.w.fingertip(), 3).tolist()}
        self.check_tracking(start - work * PUSH_STANDOFF, "push standoff")
        yield from self.reach_planned(start - work * PUSH_STANDOFF, start, orientation, PUSH_INSERT_SPEED)
        trace["start_tip"] = np.round(self.w.fingertip(), 3).tolist()
        self.check_tracking(start, "push start")
        plan, previous = [], self.w.arm_target.copy()
        for k in range(1, int(distance / PUSH_PLAN_STEP) + 1):
            point = start + side * lateral * k * PUSH_PLAN_STEP
            try:
                joints = self.w.solve_ik(point, orientation, previous)
            except SimError:
                break
            if float(np.abs(joints - previous).max()) > PUSH_MAX_JOINT_STEP:
                break
            plan.append((point, joints))
            previous = joints
        trace["planned_m"] = round(len(plan) * PUSH_PLAN_STEP, 3)
        trace["target_lateral"] = round(float(target.center @ lateral), 3)
        trace["target_debug"] = {"ycb": target_box.ycb, "center": np.round(target.center, 3).tolist(), "extents": np.round(target.extents, 3).tolist(),
                                 "axes": np.round(target.axes, 2).tolist(), "round": target.round_axis, "travel": round(travel, 3)}
        reached = start.copy()
        speed = args.push_speed or PUSH_STROKE_SPEED
        trace["push_speed"] = speed
        ticks = max(1, int(PUSH_PLAN_STEP / speed / PHYSICS_DT))
        for k, (point, joints) in enumerate(plan):
            if k % int(PUSH_STEP / PUSH_PLAN_STEP) == 0 and side * (float((self.w.sync(blocker) - origin) @ lateral) - goal_lateral) >= 0:
                break
            begin = self.w.arm_target.copy()
            for t in range(1, ticks + 1):
                self.w.arm_target = begin + (joints - begin) * t / ticks
                yield
            reached = point
        trace["arm_moved_m"] = round(float((self.w.sync(blocker) - origin) @ lateral) * side, 3)
        remaining = side * (goal_lateral - float((self.w.sync(blocker) - origin) @ lateral))
        reverse_base = None
        pose = self.true_target(blocker)
        upright = abs(float(pose.axes[2, up_axis])) > UPRIGHT_COS
        trace["upright"] = upright
        if self.base_carry and 0 < remaining <= PUSH_MAX_CARRY and trace["arm_moved_m"] > -0.02 and upright:
            reverse_base = self.w.base_target.copy()
            carried = self.w.base_target.copy()
            carried[:2] += side * lateral[:2] * (remaining + PUSH_OVERSHOOT)
            yield from self.drive_to(carried, PUSH_SPEED)
            trace["base_carry_m"] = round(remaining + PUSH_OVERSHOOT, 3)
        trace["goal_lateral"] = round(travel, 3)
        trace["final_lateral"] = round(float((self.w.sync(blocker) - origin) @ lateral) * side, 3)
        trace["final_depth"] = round(float((self.w.sync(blocker) - origin) @ work), 3)
        pose = self.true_target(blocker)
        trace["upright"] = bool(abs(float(pose.axes[2, up_axis])) > UPRIGHT_COS)
        trace["end_tip"] = np.round(self.w.fingertip(), 3).tolist()
        trace["end_cmd"] = np.round(reached, 3).tolist()
        self.push_trace = trace
        path, self.w.trail = self.w.trail, None
        if reverse_base is not None:
            yield from self.drive_to(reverse_base, PUSH_SPEED * 3)
        for joints in reversed(path):
            self.w.arm_target = joints
            yield
        self.recording = False

    def push_vla(self, instruction: str, blocker: Item, origin: np.ndarray, lateral: np.ndarray, goal_lateral: float):
        ready_tip = self.w.fingertip().copy()
        reached_goal = False
        self.vla_debug = {"ik_failures": 0, "actions": 0, "first_actions": [], "first_state": None}
        previous = None
        for _ in range(VLA_PUSH_QUERIES * VLA_EXECUTE_ACTIONS // args.push_execute):
            obs = self.w.policy_observation()
            state = np.concatenate([obs["state"], marker_center(obs["head"])]) if args.marker_state else obs["state"]
            body = json.dumps({
                "instruction": instruction, "state": state.tolist(),
                "images": {"head": encode_jpeg(obs["head"]), "wrist": encode_jpeg(obs["wrist"])},
            }).encode()
            request = urllib.request.Request(args.policy_url + "/act", data=body, headers={"content-type": "application/json"})
            with urllib.request.urlopen(request, timeout=30) as response:
                actions = np.array(json.loads(response.read())["actions"], dtype=float)
            if previous is not None and args.push_blend:
                overlap = min(args.push_blend, len(previous) - args.push_execute, len(actions))
                weights = (np.arange(1, overlap + 1) / (overlap + 1))[:, None]
                actions[:overlap] = weights * actions[:overlap] + (1 - weights) * previous[args.push_execute:args.push_execute + overlap]
            previous = actions
            if self.vla_debug["first_state"] is None:
                self.vla_debug["first_state"] = np.round(obs["state"], 3).tolist()
                self.vla_debug["first_actions"] = np.round(actions[:3], 3).tolist()
            for index in range(args.push_execute):
                action = actions[min(index + args.push_lead, len(actions) - 1)]
                start_arm = self.w.arm_target.copy()
                self.vla_debug["actions"] += 1
                try:
                    goal_arm = self.w.solve_ik(self.w.to_world_frame(action[:3]), self.w.push_orientation(action[3:9]), start_arm)
                except SimError as err:
                    self.vla_debug["ik_failures"] += 1
                    self.vla_debug["ik_error"] = str(err)[:200]
                    goal_arm = start_arm
                for i in range(1, CONTROL_EVERY + 1):
                    self.w.arm_target = start_arm + (goal_arm - start_arm) * i / CONTROL_EVERY
                    yield
                target_tip = self.w.to_world_frame(action[:3])
                for _ in range(args.push_settle):
                    if np.linalg.norm(self.w.fingertip() - target_tip) < VLA_PUSH_SETTLE_TOLERANCE:
                        break
                    yield
            moved = float((self.w.sync(blocker) - origin) @ lateral)
            reached_goal = reached_goal or moved * np.sign(goal_lateral) >= abs(goal_lateral)
            if reached_goal and np.linalg.norm(self.w.fingertip() - ready_tip) < VLA_PUSH_HOME_TOLERANCE:
                return

    def query_push_policy(self, instruction: str) -> tuple[dict, np.ndarray]:
        obs = self.w.policy_observation()
        state = np.concatenate([obs["state"], marker_center(obs["head"])]) if args.marker_state else obs["state"]
        body = json.dumps({
            "instruction": instruction, "state": state.tolist(),
            "images": {"head": encode_jpeg(obs["head"]), "wrist": encode_jpeg(obs["wrist"])},
        }).encode()
        request = urllib.request.Request(args.policy_url + "/act", data=body, headers={"content-type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return obs, np.array(json.loads(response.read())["actions"], dtype=float)

    def command_push_target(self, position: np.ndarray, rotation: np.ndarray) -> bool:
        try:
            joints = self.w.solve_ik(self.w.to_world_frame(position), self.w.push_orientation(rotation_to_6d(rotation)), self.w.arm_target)
        except SimError:
            return False
        step = joints - self.w.arm_target
        self.teleop_joint_jump = float(np.abs(step).max())
        if self.teleop_joint_jump > TELEOP_IK_JUMP:
            return False
        self.w.arm_target = self.w.arm_target + np.clip(step, -TELEOP_MAX_JOINT_STEP, TELEOP_MAX_JOINT_STEP)
        return True

    def push_teleop(self, instruction: str, blocker: Item, origin: np.ndarray, lateral: np.ndarray, side: float):
        items = [(item, *item.prim.get_world_pose()) for item in self.w.boxes_at(blocker.location_id, background=True)]
        ready_joints = self.w.robot.get_joint_positions()[self.w.arm_idx].copy()
        ready_base = self.w.base_target.copy()
        retries = 0
        while True:
            _, orientation = self.w.hand.get_world_pose()
            target_position = self.w.to_base_frame(self.w.fingertip())
            target_rotation = self.w.base_rotation().T @ quat_to_rot_matrix(orientation)
            controller = "vla" if TELEOP.assist else "human"
            TELEOP.update(phase="driving", retries=retries)
            self.teleop_frames = {"head": [], "wrist": [], "state": [], "action": [], "human": []}
            result = yield from self.teleop_episode(instruction, blocker, origin, lateral, side,
                                                    target_position, target_rotation, controller)
            if result != "retry":
                self.teleop_result = result
                return
            retries += 1
            for item, position, rotation in items:
                self.w.set_dynamic(item, False)
                item.prim.set_world_pose(position, rotation)
            positions = self.w.robot.get_joint_positions()
            positions[self.w.arm_idx] = ready_joints
            positions[self.w.base_idx] = ready_base
            self.w.base_target = ready_base.copy()
            self.w.robot.set_joint_positions(positions)
            self.w.robot.set_joint_velocities(np.zeros_like(positions))
            self.w.arm_target = ready_joints.copy()
            for item, _, _ in items:
                self.w.set_dynamic(item, True)
            for _ in range(10):
                yield

    def teleop_episode(self, instruction: str, blocker: Item, origin: np.ndarray, lateral: np.ndarray, side: float,
                       target_position: np.ndarray, target_rotation: np.ndarray, controller: str):
        chunk, chunk_index, previous_chunk, vla_from, vla_to, tick = None, 0, None, None, None, 0
        last_reachable = (target_position.copy(), target_rotation.copy())
        floor = self.w.surface_z(blocker.location_id) + PUSH_HEIGHT - TELEOP_FLOOR_MARGIN
        while True:
            command = TELEOP.pop_command()
            if command == "save":
                return "save"
            if command == "next":
                return "discard"
            if command == "discard":
                return "retry"
            if command == "release" and TELEOP.assist:
                controller, chunk = "vla", None
            keys = TELEOP.active_keys()
            if controller == "vla" and keys & TELEOP_MOTION_KEYS:
                controller = "human"
            if TELEOP.paused:
                TELEOP.update(controller=controller, paused=True)
                yield
                continue
            if controller == "human":
                speed = TELEOP_FINE_SPEED if "shift" in keys else TELEOP_SPEED
                velocity = sum((TELEOP_DIRECTIONS[key] for key in keys if key in TELEOP_DIRECTIONS), np.zeros(3))
                target_position = target_position + velocity * speed * PHYSICS_DT
                target_position[2] = max(target_position[2], floor)
                spin = (("q" in keys) - ("e" in keys)) * (TELEOP_FINE_TURN if "shift" in keys else TELEOP_TURN) * PHYSICS_DT
                if spin:
                    target_rotation = np.array([[math.cos(spin), -math.sin(spin), 0], [math.sin(spin), math.cos(spin), 0], [0, 0, 1]]) @ target_rotation
            elif tick % CONTROL_EVERY == 0:
                if chunk is None or chunk_index >= args.push_execute:
                    _, fresh = self.query_push_policy(instruction)
                    if previous_chunk is not None and args.push_blend:
                        overlap = min(args.push_blend, len(previous_chunk) - args.push_execute, len(fresh))
                        weights = (np.arange(1, overlap + 1) / (overlap + 1))[:, None]
                        fresh[:overlap] = weights * fresh[:overlap] + (1 - weights) * previous_chunk[args.push_execute:args.push_execute + overlap]
                    chunk = previous_chunk = fresh
                    chunk_index = 0
                action = chunk[min(chunk_index + args.push_lead, len(chunk) - 1)]
                chunk_index += 1
                vla_from = (target_position.copy(), target_rotation.copy())
                vla_to = (action[:3], rotation_from_6d(action[3:9]))
            if controller == "vla" and vla_to is not None:
                t = (tick % CONTROL_EVERY + 1) / CONTROL_EVERY
                target_position = vla_from[0] + (vla_to[0] - vla_from[0]) * t
                target_rotation = rotation_from_6d(rotation_to_6d(vla_from[1]) + (rotation_to_6d(vla_to[1]) - rotation_to_6d(vla_from[1])) * t)
            reachable = self.command_push_target(target_position, target_rotation)
            if reachable:
                last_reachable = (target_position.copy(), target_rotation.copy())
            elif controller == "human":
                target_position, target_rotation = last_reachable[0].copy(), last_reachable[1].copy()
            active = controller == "vla" or bool(keys & TELEOP_MOTION_KEYS)
            if tick % CONTROL_EVERY == 0:
                obs = self.w.policy_observation()
                TELEOP.set_cameras(obs["head"], obs["wrist"])
                if active:
                    self.teleop_frames["head"].append(obs["head"])
                    self.teleop_frames["wrist"].append(obs["wrist"])
                    self.teleop_frames["state"].append(obs["state"])
                    self.teleop_frames["action"].append(np.concatenate([target_position, rotation_to_6d(target_rotation), [self.w.finger_target]]))
                    self.teleop_frames["human"].append(controller == "human")
            moved = float((self.w.sync(blocker) - origin) @ lateral) * side
            TELEOP.update(controller=controller, paused=False, reachable=reachable, frames=len(self.teleop_frames["state"]),
                          moved=round(moved, 3), tip=np.round(target_position, 3).tolist(),
                          base=np.round(self.w.base_pose(), 3).tolist(), joint_jump=round(getattr(self, "teleop_joint_jump", 0.0), 3))
            tick += 1
            yield

    def reach_seed(self) -> np.ndarray:
        return getattr(self, "push_joints", None) if getattr(self, "push_joints", None) is not None else NATURAL_REACH

    def natural_orientation(self, position: np.ndarray, orientation: np.ndarray) -> np.ndarray:
        flipped = rot_matrix_to_quat(quat_to_rot_matrix(orientation) @ np.diag([-1.0, -1.0, 1.0]))
        best, best_cost = orientation, math.inf
        for candidate in (orientation, flipped):
            try:
                joints = self.w.solve_ik(position, candidate, self.reach_seed())
            except SimError:
                continue
            cost = float(np.abs(joints - self.reach_seed()).sum())
            if cost < best_cost:
                best, best_cost = candidate, cost
        return best

    def plan_line(self, origin: np.ndarray, goal: np.ndarray, orientation: np.ndarray,
                  max_step: float = PUSH_MAX_JOINT_STEP) -> list[np.ndarray]:
        steps = max(1, int(float(np.linalg.norm(goal - origin)) / PUSH_PLAN_STEP))
        previous = self.w.solve_ik(goal, orientation, self.reach_seed())
        plan = [previous]
        for k in range(steps - 1, -1, -1):
            joints = self.w.solve_ik(origin + (goal - origin) * k / steps, orientation, previous)
            if float(np.abs(joints - previous).max()) > max_step:
                raise SimError("ik_jump", f"arm configuration jump on the way to {np.round(goal, 2).tolist()} at step {steps - k}/{steps}: "
                                          f"{np.round(previous, 2).tolist()} -> {np.round(joints, 2).tolist()}")
            plan.append(joints)
            previous = joints
        return plan[::-1]

    def follow(self, plan: list[np.ndarray], length: float, speed: float):
        speed *= MOTION_SPEEDUP
        ticks = max(1, int(length / max(1, len(plan) - 1) / speed / PHYSICS_DT))
        for joints in plan[1:]:
            begin = self.w.arm_target.copy()
            for t in range(1, ticks + 1):
                self.w.arm_target = begin + (joints - begin) * t / ticks
                yield
        yield from self.settle_arm()

    def approach_along(self, origin: np.ndarray, goal: np.ndarray, orientation: np.ndarray, speed: float, stage: str):
        plan = self.plan_line(origin, goal, orientation)
        self.last_plan_goal = plan[-1]
        yield from self.move_arm(plan[0], 90)
        self.check_tracking(origin, stage)
        yield from self.follow(plan, float(np.linalg.norm(goal - origin)), speed)

    def reach_planned(self, origin: np.ndarray, goal: np.ndarray, orientation: np.ndarray, speed: float):
        speed *= MOTION_SPEEDUP
        steps = max(1, int(float(np.linalg.norm(goal - origin)) / PUSH_PLAN_STEP))
        plan, previous = [], self.w.arm_target.copy()
        for k in range(1, steps + 1):
            joints = self.w.solve_ik(origin + (goal - origin) * k / steps, orientation, previous)
            if float(np.abs(joints - previous).max()) > PUSH_MAX_JOINT_STEP:
                raise SimError("ik_jump", f"arm configuration jump on the way to {np.round(goal, 2).tolist()}")
            plan.append(joints)
            previous = joints
        ticks = max(1, int(float(np.linalg.norm(goal - origin)) / steps / speed / PHYSICS_DT))
        for joints in plan:
            begin = self.w.arm_target.copy()
            for t in range(1, ticks + 1):
                self.w.arm_target = begin + (joints - begin) * t / ticks
                yield
        for _ in range(10):
            yield

    def check_tracking(self, commanded: np.ndarray, stage: str) -> None:
        error = float(np.linalg.norm(self.w.fingertip() - commanded))
        if error > PUSH_TRACK_TOLERANCE:
            joints = self.w.robot.get_joint_positions()[self.w.arm_idx]
            raise SimError("tracking", f"fingertip {error:.2f} m off the {stage}: commanded {np.round(commanded, 3).tolist()} "
                           f"tip {np.round(self.w.fingertip(), 3).tolist()} base {np.round(self.w.base_pose(), 3).tolist()} "
                           f"target {np.round(self.w.arm_target, 2).tolist()} joints {np.round(joints, 2).tolist()}")

    def take_tool(self, name: str):
        if self.w.tool == name:
            return
        if self.w.tool:
            yield from self.return_tool()
        slot, rotation = self.w.tool_slot(name)
        orientation = rot_matrix_to_quat(rotation @ TOOL_MOUNTS[name].T)
        up = np.array([0.0, 0.0, TOOL_HOVER])
        yield from self.gripper(0.04)
        try:
            hover = self.w.solve_ik(slot + up, orientation)
        except SimError:
            yield from self.move_arm(self.w.work_home, 60)
            hover = self.w.solve_ik(slot + up, orientation, self.w.work_home)
        yield from self.move_arm(hover, 90)
        yield from self.reach_linear(slot, orientation, TOOL_SPEED)
        self.check_tracking(slot, f"{name} slot")
        self.w.tool = name
        yield from self.gripper(TOOL_GRIP[name])
        yield from self.reach_linear(self.w.fingertip() + up, orientation, TOOL_SPEED)
        yield from self.move_arm(self.w.work_home, 70)

    def return_tool(self):
        name = self.w.tool
        if not name:
            return
        slot, rotation = self.w.tool_slot(name)
        orientation = rot_matrix_to_quat(rotation @ TOOL_MOUNTS[name].T)
        up = np.array([0.0, 0.0, TOOL_HOVER])
        tip = rotation @ TOOL_TIPS[name]
        yield from self.move_arm(self.w.solve_ik(slot + tip + up, orientation), 90)
        yield from self.reach_linear(slot + tip, orientation, TOOL_SPEED)
        self.w.tool = None
        yield from self.gripper(0.04)
        yield from self.reach_linear(slot + up, orientation, TOOL_SPEED)
        yield from self.move_arm(self.w.work_home, 70)

    def clear_blockers(self, box: Item):
        blockers = self.blockers(box)
        if not blockers:
            return
        yield from self.take_tool("paddle")
        stance = self.w.base_target.copy()
        work = np.array([math.cos(self.work_yaw()), math.sin(self.work_yaw())])
        backed = stance.copy()
        backed[:2] -= work * PUSH_BACKOFF
        yield from self.drive_to(backed)
        yield from self._push_all(box, blockers)
        yield from self.drive_to(stance)
        yield from self.return_tool()

    def _push_all(self, box: Item, blockers: list[Item]):
        for blocker in blockers:
            before = self.w.sync(box).copy()
            blocker_before = self.w.sync(blocker).copy()
            yield from self.push_aside(blocker, box)
            work = np.array([math.cos(self.work_yaw()), math.sin(self.work_yaw()), 0.0])
            lateral = np.array([-work[1], work[0], 0.0])
            gap = self.w.sync(blocker) - self.w.sync(box)
            self.last_grasp = {**self.last_grasp, "blocker_pushed_m": round(float(np.linalg.norm(self.w.sync(blocker) - blocker_before)), 3),
                               "blocker_lateral_m": round(float(abs(gap @ lateral)), 3), "blocker_depth_m": round(float(gap @ work), 3),
                               "blocker_tilt": round(float(self.true_target(blocker).axes[2, 2]), 2)}
            self.last_grasp = {**self.last_grasp, "push_trace": getattr(self, "push_trace", None), "pushed": [*self.last_grasp.get("pushed", []), blocker.ycb],
                               "push_moved_target": round(float(np.linalg.norm(self.w.sync(box) - before)), 3)}

    def hold_offset(self) -> np.ndarray:
        _, orientation = self.w.hand.get_world_pose()
        return quat_to_rot_matrix(orientation).T @ (self.w.sync(self.w.held) - self.w.fingertip())

    def tip_for(self, box_center: np.ndarray, orientation: np.ndarray, offset: np.ndarray) -> np.ndarray:
        return box_center - quat_to_rot_matrix(orientation) @ offset

    def set_down(self, box: Item, surface: np.ndarray, yaw: float, clearance: float, direct: bool = False):
        offset = self.hold_offset()
        center = surface + np.array([0.0, 0.0, box.footprint[2] / 2 + SURFACE_LIFT])
        choice = self.grasp_choice or {"pitch": GRASP_PITCH, "roll": 0.0}
        candidates = [rot_matrix_to_quat(tool_rotation(yaw + turn, pitch, choice["roll"]))
                      for pitch in (choice["pitch"],) for turn in SET_DOWN_TURNS]
        current = np.asarray(self.w.hand.get_world_pose()[1], dtype=float)
        candidates.sort(key=lambda q: -abs(float(np.dot(q, current))))
        plan = None
        for orientation in candidates:
            tip = self.tip_for(center, orientation, offset)
            above = tip + np.array([0.0, 0.0, clearance])
            try:
                hover = above if direct else above + HOVER_LIFT
                hover_joints = self.w.solve_ik(hover, orientation, self.w.work_home)
                above_joints = self.w.solve_ik(above, orientation, hover_joints)
                self.w.solve_ik(tip, orientation, above_joints)
            except SimError:
                continue
            plan = tip, above, orientation, hover_joints
            break
        if plan is None:
            raise SimError("ik_failed", f"no arm solution to set {box.sku} down at {np.round(center, 2).tolist()}")
        tip, above, orientation, hover_joints = plan
        if direct:
            yield from self.reach_linear(above, orientation, 0.3)
        else:
            if np.abs(self.w.arm_target - self.w.work_home).max() > 0.05:
                yield from self.move_arm(self.w.work_home, 60)
            yield from self.move_arm(hover_joints, 80)
        yield from self.reach_linear(above, orientation, 0.2)
        yield from self.reach_linear(tip, orientation, 0.15)
        self.w.held = None
        return tip, above, orientation

    def stash(self, box: Item):
        slot = self.w.free_deck_slot()
        surface, yaw = self.w.deck_slot(slot)
        rim = ROBOT_TOTE_SIZE[2] - TOTE_WALL
        side_grip = (self.grasp_choice or {"pitch": GRASP_PITCH})["pitch"] < 1.0
        lift = max(0.0, rim + HAND_RIM_CLEARANCE - box.footprint[2] / 2) if side_grip else 0.0
        release = surface + np.array([0.0, 0.0, lift])
        tip, above, orientation = yield from self.set_down(box, release, yaw, CARRY_CLEARANCE)
        box.location_id = None
        box.deck_slot = slot
        self.w.tote.append(box)
        box.pinned = box.footprint[2] > 2 * ROBOT_TOTE_SIZE[2]
        if box.pinned:
            box.rest_on(surface, yaw)
        else:
            self.w.set_dynamic(box, True)
        yield from self.gripper(0.04)
        yield from self.reach_linear(above, orientation, 0.25)
        yield from self.reach_linear(above + HOVER_LIFT, orientation, 0.25)
        yield from self.move_arm(self.w.work_home, 60)

    def take_from_deck(self, box: Item):
        self.target = self.true_target(box)
        options = self.grasp_options(self.target, self.target.center[2] - self.target.height / 2)
        reachable = None
        for option in sorted(options, key=lambda o: o["pitch"] < 1.0):
            for lift in TOTE_HOVER_LIFTS:
                above = option["position"] + np.array([0.0, 0.0, CARRY_CLEARANCE])
                try:
                    hover_joints = self.w.solve_ik(above + np.array([0.0, 0.0, lift]), option["orientation"])
                    joints = self.w.solve_ik(above, option["orientation"], hover_joints)
                    self.w.solve_ik(option["position"], option["orientation"], joints)
                except SimError:
                    continue
                reachable = option, hover_joints, lift
                break
            if reachable:
                break
        if reachable is None:
            raise SimError("ik_failed", f"no reachable grasp for {box.sku} in the tote")
        self.grasp_choice, hover_joints, lift = reachable
        position, orientation = self.grasp_choice["position"], self.grasp_choice["orientation"]
        above = position + np.array([0.0, 0.0, CARRY_CLEARANCE])
        hover = above + np.array([0.0, 0.0, lift])
        yield from self.gripper(0.04)
        tip = self.w.fingertip()
        for height in (tip[2], *TOTE_LIFT_HEIGHTS):
            if height <= hover[2] + 0.05:
                break
            level = np.array([hover[0], hover[1], min(height, tip[2])])
            try:
                self.w.solve_ik(level, orientation)
            except SimError:
                continue
            yield from self.reach_linear(level, orientation, 0.3)
            break
        yield from self.reach_linear(hover, orientation, 0.3)
        yield from self.reach_linear(above, orientation, 0.2)
        yield from self.reach_linear(position, orientation, 0.15)
        self.w.finger_target = 0.0
        for _ in range(CLOSE_STEPS):
            yield
        self.grasp_roll = self.grasp_choice["roll"]
        self.w.tote.remove(box)
        self.w.set_dynamic(box, False)
        self.w.grab(box)
        yield from self.reach_linear(above, orientation, 0.2)
        yield from self.reach_linear(hover, orientation, 0.2)

    def grasp_with_reposition(self, box: Item):
        try:
            yield from self.grasp_scripted(box)
            return
        except SimError as error:
            if error.code != "ik_failed":
                raise
        local = self.w.to_base_frame(self.w.sync(box))
        forward = float(np.clip(local[0] - ARM_MOUNT - COMFORT_REACH, -REPOSITION_LIMIT, REPOSITION_LIMIT))
        target = self.w.base_target.copy()
        heading = target[2]
        target[:2] += np.array([math.cos(heading), math.sin(heading)]) * forward
        target[:2] += np.array([-math.sin(heading), math.cos(heading)]) * float(local[1])
        yield from self.move_arm(self.w.work_home, 40)
        yield from self.drive_to(target)
        yield from self.grasp_scripted(box)

    def grasp_scripted(self, box: Item):
        self.last_grasp = {}
        yield from self.face_work()
        coarse = yield from self.locate(box)
        yield from self.align_to(coarse.center)
        if self.pose_source == "sim" and self.w.locations[box.location_id]["kind"] == "storage":
            yield from self.clear_blockers(box)
        yield from self.prepare(box)
        yield from self.arm_grasp(box)
        return True

    def arm_grasp(self, box: Item):
        position, direction, orientation = (self.grasp_choice[k] for k in ("position", "direction", "orientation"))
        pregrasp = position - direction * GRASP_STANDOFF
        yield from self.reach_planned(self.w.fingertip(), pregrasp, orientation, 0.15)
        pregrasp_moved = float(np.linalg.norm(self.w.sync(box) - position))
        neighbours = {o.ycb: self.w.sync(o).copy() for o in self.w.boxes_at(box.location_id, background=True) if o is not box}
        target_before = self.w.sync(box).copy()
        rotation = quat_to_rot_matrix(orientation)
        grip_width = round(self.target.grasp_width(rotation[:, 1], rotation[:, 2]), 3)
        open_width = float(self.w.robot.get_joint_positions()[self.w.finger_idx].sum())
        pregrasp_error = float(np.abs(self.w.robot.get_joint_positions()[self.w.arm_idx] - self.w.arm_target).max())
        yield from self.reach_planned(pregrasp, position - direction * GRASP_DEPTH, orientation, 0.1)
        reach_error = (self.w.fingertip() - position).round(3).tolist()
        joint_error = float(np.abs(self.w.robot.get_joint_positions()[self.w.arm_idx] - self.w.arm_target).max())
        approach_moved = round(float(np.linalg.norm(self.w.sync(box) - target_before)), 3)
        self.w.finger_target = 0.0
        for _ in range(CLOSE_STEPS):
            yield
        if float(np.linalg.norm(self.w.fingertip() - self.w.sync(box))) < ATTACH_ON_CLOSE:
            self.w.set_dynamic(box, False)
            self.w.grab(box)
        self.last_grasp = {**self.last_grasp, "approach_moved": approach_moved,
                           "close_moved": round(float(np.linalg.norm(self.w.sync(box) - target_before)), 3),
                           "grip_width": grip_width, "open_width": round(open_width, 3),
                           "neighbours_moved": {o.ycb: round(float(np.linalg.norm(self.w.sync(o) - neighbours[o.ycb])), 3)
                                                for o in self.w.boxes_at(box.location_id, background=True) if o.ycb in neighbours
                                                and np.linalg.norm(self.w.sync(o) - neighbours[o.ycb]) > 0.005},
                           "reach_error": reach_error, "joint_error": round(joint_error, 3),
                           "pregrasp_error": round(pregrasp_error, 3), "pregrasp_moved": round(pregrasp_moved, 3),
                           "fingertip": np.round(self.w.fingertip(), 3).tolist(),
                           "hand": np.round(self.w.hand.get_world_pose()[0], 3).tolist(), "target": np.round(position, 3).tolist(),
                           "opening": float(self.w.robot.get_joint_positions()[self.w.finger_idx].mean()),
                           "moved": float(np.linalg.norm(self.w.sync(box) - position))}

    def lift(self, box: Item, start_z: float):
        direction, orientation = self.grasp_choice["direction"], self.grasp_choice["orientation"]
        if args.grasp == "kinematic":
            truth = self.true_target(box)
            hand = quat_to_rot_matrix(self.w.hand.get_world_pose()[1])
            between = bool(truth.contains(self.w.fingertip(), ATTACH_MARGIN)
                           and truth.grasp_width(hand[:, 1], hand[:, 2]) <= MAX_GRASP_WIDTH + 0.005)
            self.last_grasp = {**self.last_grasp, "between_fingers": between}
            if between:
                self.w.set_dynamic(box, False)
                self.w.grab(box)
        tip = self.w.fingertip()
        up = np.array([0, 0, 1.0])
        out = np.array([direction[0], direction[1], 0.0])
        if np.linalg.norm(out) < 0.2:
            out = np.array([math.cos(self.work_yaw()), math.sin(self.work_yaw()), 0.0])
        out /= float(np.linalg.norm(out))
        in_rack = box.location_id in self.w.locations and self.w.locations[box.location_id]["kind"] == "storage"
        top_down_rise = SHELF_TOP_DOWN_LIFT if in_rack else TOP_DOWN_LIFT
        rise = up * (top_down_rise if self.grasp_choice["pitch"] > 1.0 else 0.03)
        yield from self.reach_first([tip + rise], orientation, 0.1)
        yield from self.reach_first([tip + rise - out * RETRACT, tip + rise - out * RETRACT / 2], orientation, 0.2)
        for _ in range(HOLD_STEPS):
            yield
        position = self.w.sync(box)
        self.last_grasp = {**getattr(self, "last_grasp", {}), "lift_dz": round(float(position[2] - start_z), 3),
                           "lift_tip_dist": round(float(np.linalg.norm(self.w.fingertip() - position)), 3)}
        return self.w.holding(box, start_z)

    def set_marker(self, box: Item, rng: np.random.Generator | None = None) -> np.ndarray:
        rng = rng or np.random.default_rng()
        self.w.marker = self.true_target(box).center + np.append(rng.normal(0.0, MARKER_NOISE, 2), 0.0)
        return self.w.marker

    def grasp_vla(self, box: Item, instruction: str):
        self.last_grasp = {}
        yield from self.face_work()
        yield from self.align_to(self.set_marker(box))
        self.w.look_z = self.w.surface_z(box.location_id) + HEAD_LOOK_ABOVE
        yield from self.gripper(0.04)
        position = self.w.sync(box)
        self.last_vla_miss = None
        try:
            for _ in range(VLA_MAX_STEPS):
                obs = self.w.policy_observation()
                body = json.dumps({
                    "instruction": instruction, "state": obs["state"].tolist(),
                    "images": {"head": encode_jpeg(obs["head"]), "wrist": encode_jpeg(obs["wrist"])},
                }).encode()
                request = urllib.request.Request(args.policy_url + "/act", data=body, headers={"content-type": "application/json"})
                with urllib.request.urlopen(request, timeout=30) as response:
                    actions = np.array(json.loads(response.read())["actions"], dtype=float)
                for action in actions[:VLA_EXECUTE_ACTIONS]:
                    start_arm, start_finger = self.w.arm_target.copy(), self.w.finger_target
                    try:
                        goal_arm = self.w.solve_ik(self.w.to_world_frame(action[:3]), self.w.tool_orientation(action[3], action[4], action[5]))
                    except SimError:
                        goal_arm = start_arm
                    for i in range(1, CONTROL_EVERY + 1):
                        t = i / CONTROL_EVERY
                        self.w.arm_target = start_arm + (goal_arm - start_arm) * t
                        self.w.finger_target = float(np.clip(start_finger + (action[6] - start_finger) * t, 0.0, 0.04))
                        yield
                    if self.w.finger_target < 0.015:
                        for _ in range(CLOSE_STEPS):
                            yield
                        _, orientation = self.w.hand.get_world_pose()
                        rotation = quat_to_rot_matrix(orientation)
                        self.grasp_choice = {"direction": rotation[:, 2], "orientation": orientation, "roll": 0.0,
                                             "pitch": float(self.w.tool_pose()[4])}
                        self.grasp_roll = 0.0
                        self.last_vla_miss = float(np.linalg.norm(self.w.fingertip() - position))
                        return self.last_vla_miss < HOLD_TOLERANCE
            return False
        finally:
            self.w.marker = None

    def pick(self, sku: str, location_id: str, fail: bool = False, policy: str = "scripted",
             point: list[float] | None = None):
        self.pose_source = "sim" if policy == "expert" else args.pose
        if point is None:
            box = self.target_box(sku, location_id)
        else:
            box = self.grounded_box(location_id, point)
            if box.sku != sku:
                raise SimError("wrong_object", f"grounded point at {location_id} is a {box.ycb[4:].replace('_', ' ')}, not {sku}")
        if policy != "expert" and self.w.locations[location_id]["kind"] == "storage":
            blocking = self.blockers(box)
            if blocking:
                names = ", ".join(BLOCKER_NAMES.get(b.ycb, b.ycb[4:].replace("_", " ")) for b in blocking)
                raise SimError("blocked", f"{sku} at {location_id} is behind the {names}; push it aside with the paddle before picking")
        yield from self.approach_rack(location_id)
        position, _ = box.prim.get_world_pose()
        used = "scripted"
        vla_success = None
        instruction = None
        if policy in ("vla", "vla_with_fallback"):
            instruction = instruction_for(self.w, sku)
            vla_success = yield from self.grasp_vla(box, instruction)
            used = "vla"
            if not vla_success:
                yield from self.gripper(0.04)
                yield from self.move_arm(self.w.arm_home, 50)
                if policy == "vla":
                    raise SimError("grasp_failed", f"VLA grasp of {sku} at {location_id} missed")
                used = "scripted_fallback"
                yield from self.grasp_scripted(box)
        else:
            yield from self.grasp_with_reposition(box)
        held = yield from self.lift(box, position[2])
        if fail or not held:
            self.w.held = None
            self.w.set_dynamic(box, True)
            yield from self.gripper(0.04)
            yield from self.move_arm(self.w.work_home, 60)
            reason = "slipped from gripper" if held else f"was not lifted ({self.last_grasp})"
            raise SimError("grasp_failed", f"{sku} {reason} at {location_id}")
        if self.w.held is not box:
            self.w.set_dynamic(box, False)
            self.w.grab(box)
        from_rack = self.w.locations[location_id]["kind"] == "storage"
        box.location_id = None
        if from_rack:
            yield from self.drive_to(self.retreat_pose(self.w.base_target.copy()))
        yield from self.stash(box)
        yield from self.return_tool()
        self.w.repack(location_id)
        return {"picked": sku, "tote": [b.sku for b in self.w.tote], "policy_used": used, "vla_success": vla_success,
                "instruction": instruction, "grasp": {k: self.last_grasp.get(k) for k in ("yaw", "pitch", "roll")}}

    def approach_rack(self, location_id: str):
        if self.w.locations[location_id]["kind"] != "storage":
            return
        stance = self.w.approach_pose(location_id)
        stance[1] = self.w.base_target[1]
        if np.linalg.norm(stance[:2] - self.w.base_target[:2]) > 0.02:
            yield from self.drive_to(stance)

    def retreat_pose(self, stance: np.ndarray) -> np.ndarray:
        heading = float(stance[2])
        return stance - np.array([math.cos(heading), math.sin(heading), 0.0]) * RACK_RETREAT

    def push_blocker(self, sku: str, location_id: str, policy: str = "vla"):
        box = self.target_box(sku, location_id)
        blocking = self.blockers(box)
        if not blocking:
            return {"pushed": [], "policy": policy}
        if self.w.tool != "paddle":
            raise SimError("no_tool", "pick up the paddle before pushing")
        yield from self.move_arm(self.w.work_home, 60)
        pushed = []
        self.w.push_encoding = policy == "vla"
        try:
            for blocker in blocking:
                self.w.marker = self.true_target(blocker).center.copy()
                yield from self.push_aside(blocker, box, policy)
                pushed.append({"blocker": BLOCKER_NAMES.get(blocker.ycb, blocker.ycb), **{
                    k: self.push_trace.get(k) for k in ("instruction", "final_lateral", "goal_lateral", "upright")}})
        finally:
            self.w.push_encoding = False
            self.w.marker = None
        yield from self.move_arm(self.w.work_home, 60)
        remaining = self.blockers(box)
        if remaining:
            raise SimError("push_failed", f"{sku} at {location_id} is still blocked after the push")
        return {"pushed": pushed, "policy": policy}

    def place(self, location_id: str):
        location = self.w.locations[location_id]
        if location["kind"] != "dispatch":
            placed = []
            for box in list(self.w.tote):
                yield from self.stow(box.sku, location_id)
                placed.append(box.sku)
            return {"placed": placed}
        self.w.show_tote(location_id)
        self.dispatch_location = location_id
        yield from self.face_work()
        free = self.w.slot_surfaces(location_id, OUTBOUND_SLOTS)
        for occupant in self.w.boxes_at(location_id):
            free = [spot for spot in free if np.linalg.norm(spot[:2] - occupant.position[:2]) > 0.05]
        placed = []
        stance = self.w.approach_pose(location_id)
        for box in sorted(self.w.tote, key=lambda b: b.deck_slot):
            yield from self.drive_to(self.back_off(np.array([stance[0], self.w.base_target[1], stance[2]]), DISPATCH_RETREAT))
            yield from self.take_from_deck(box)
            yield from self.lift_to(TOTE_LIFT_HEIGHTS)
            surface = yield from self.drop_in_free_spot(box, free, stance)
            free = [spot for spot in free if spot is not surface]
            placed.append(box.sku)
        self.w.tote = []
        yield from self.move_arm(self.w.arm_home, 60)
        backoff = self.w.base_target.copy()
        backoff[0] -= DISPATCH_BACKOFF
        yield from self.drive_to(backoff)
        return {"placed": placed}

    def lift_to(self, heights: tuple[float, ...]):
        tip = self.w.fingertip()
        orientation = np.asarray(self.w.hand.get_world_pose()[1], dtype=float)
        for height in heights:
            target = np.array([tip[0], tip[1], height])
            try:
                self.w.solve_ik(target, orientation)
            except SimError:
                continue
            yield from self.reach_linear(target, orientation, 0.3)
            return

    def drop_plan(self, box: Item, surface: np.ndarray):
        current = quat_to_rot_matrix(np.asarray(self.w.hand.get_world_pose()[1], dtype=float))
        side_grip = abs(float(current[2, 2])) < 0.7
        lift = max(0.0, TOTE_SIZE[2] + HAND_RIM_CLEARANCE - box.footprint[2] / 2) if side_grip else 0.0
        center = surface + np.array([0.0, 0.0, box.footprint[2] / 2 + SURFACE_LIFT + lift])
        offset = self.hold_offset()
        for carry in CARRY_HEIGHTS:
            for turn in SET_DOWN_TURNS:
                spin = np.array([[math.cos(turn), -math.sin(turn), 0.0], [math.sin(turn), math.cos(turn), 0.0], [0.0, 0.0, 1.0]])
                orientation = rot_matrix_to_quat(spin @ current)
                tip = self.tip_for(center, orientation, offset)
                over = np.array([tip[0], tip[1], carry])
                try:
                    joints = self.w.solve_ik(over, orientation)
                    self.w.solve_ik(tip, orientation, joints)
                except SimError:
                    continue
                return tip, over, orientation
        raise SimError("ik_failed", f"no drop pose for {box.sku}")

    def drop_in_free_spot(self, box: Item, free: list[np.ndarray], stance: np.ndarray):
        if not free:
            raise SimError("tote_full", "no free space left in the outbound tote")
        for surface in sorted(free, key=lambda spot: (round(float(spot[0]), 2), abs(float(spot[1]) - stance[1]))):
            yield from self.drive_to(np.array([self.w.base_target[0], float(surface[1]), stance[2]]))
            yield from self.drive_to(np.array([stance[0], float(surface[1]), stance[2]]))
            try:
                tip, over, orientation = self.drop_plan(box, surface)
            except SimError:
                continue
            yield from self.reach_linear(over, orientation, 0.3)
            yield from self.reach_linear(tip, orientation, 0.12)
            self.w.held = None
            rotation = quat_to_rot_matrix(np.asarray(box.prim.get_world_pose()[1], dtype=float))
            box.location_id = self.dispatch_location
            box.rest_on(surface, math.atan2(rotation[1, 0], rotation[0, 0]))
            yield from self.gripper(0.04)
            yield from self.reach_linear(over, orientation, 0.25)
            return surface
        raise SimError("ik_failed", f"no reachable free space for {box.sku} in the outbound tote")

    def release(self, location_id: str):
        if self.w.locations[location_id]["kind"] != "dispatch":
            raise SimError("bad_request", f"{location_id} has no outbound tote")
        self.w.ship_tote(location_id)
        yield
        return {"released": location_id}

    def stow(self, sku: str, location_id: str, fail: bool = False):
        box = next((b for b in self.w.tote if b.sku == sku), None)
        if not box:
            raise SimError("item_not_found", f"{sku} not in tote")
        target, yaw = self.w.cluster_spot(location_id, box)
        yield from self.approach_rack(location_id)
        yield from self.face_work()
        yield from self.align_to(target)
        yield from self.take_from_deck(box)
        if fail:
            yield from self.stash(box)
            raise SimError("grasp_failed", f"stow of {sku} at {location_id} failed")
        offset = self.hold_offset()
        orientation = self.carry_orientation(yaw)
        direction = quat_to_rot_matrix(orientation)[:, 2]
        center = target + np.array([0.0, 0.0, box.footprint[2] / 2 + SURFACE_LIFT])
        tip = self.tip_for(center, orientation, offset)
        out = np.array([direction[0], direction[1], 0.0])
        entry = tip + np.array([0.0, 0.0, 0.03]) - out * RETRACT
        yield from self.move_arm(self.w.solve_ik(entry, orientation), 80)
        yield from self.reach_linear(tip + np.array([0.0, 0.0, 0.03]), orientation, 0.15)
        yield from self.reach_linear(tip, orientation, 0.08)
        self.w.held = None
        box.move(center, yaw)
        box.location_id = location_id
        self.w.set_dynamic(box, True)
        yield from self.gripper(0.04)
        yield from self.reach_linear(tip - out * RETRACT, orientation, 0.2)
        yield from self.move_arm(self.w.work_home, 60)
        return {"stowed": sku}


LIVE_COMMANDS = ("view", "camera", "state", "debug")
IMMEDIATE_COMMANDS = ("reset", "counts", "probe", "stage_tote", *LIVE_COMMANDS)


class Server:
    def __init__(self, warehouse: Warehouse):
        self.w = warehouse
        self.commands = Commands(warehouse)
        self.inbox: queue.Queue = queue.Queue()
        self.frame: bytes = b""
        self.frame_lock = threading.Condition()
        self.camera_lock = threading.Lock()
        self.camera_requests: list[tuple[str, dict]] = []

    def queue_camera(self, name: str, payload: dict) -> None:
        with self.camera_lock:
            self.camera_requests.append((name, payload))

    def apply_camera_requests(self) -> None:
        with self.camera_lock:
            requests, self.camera_requests = self.camera_requests, []
        for name, payload in requests:
            try:
                self.immediate(name, payload)
            except Exception:
                traceback.print_exc()

    def submit(self, name: str, payload: dict, timeout: float = 600) -> tuple[int, dict]:
        done = threading.Event()
        holder: dict = {}
        self.inbox.put((name, payload, done, holder))
        if not done.wait(timeout):
            return 504, {"error": "timeout"}
        return holder["status"], holder["body"]

    def run(self) -> None:
        current = None
        waiting: collections.deque = collections.deque()
        step = 0
        while app.is_running():
            while not self.inbox.empty():
                request = self.inbox.get()
                name, payload, done, holder = request
                if name in LIVE_COMMANDS or (name in IMMEDIATE_COMMANDS and current is None and not waiting):
                    try:
                        holder.update(status=200, body=self.immediate(name, payload))
                    except Exception as err:
                        holder.update(status=400, body={"error": "bad_request", "message": str(err)})
                    done.set()
                else:
                    waiting.append(request)
            while current is None and waiting:
                name, payload, done, holder = waiting.popleft()
                try:
                    if name in IMMEDIATE_COMMANDS:
                        holder.update(status=200, body=self.immediate(name, payload))
                        done.set()
                    else:
                        current = (getattr(self.commands, name)(**payload), done, holder)
                except Exception as err:
                    holder.update(status=400, body={"error": "bad_request", "message": str(err)})
                    done.set()
            if current:
                generator, done, holder = current
                try:
                    next(generator)
                except StopIteration as stop:
                    holder.update(status=200, body=stop.value or {})
                    done.set()
                    current = None
                except SimError as err:
                    holder.update(status=409, body={"error": err.code, "message": str(err)})
                    done.set()
                    current = None
                except Exception as err:
                    traceback.print_exc()
                    holder.update(status=500, body={"error": "internal", "message": str(err)})
                    done.set()
                    current = None
            self.w.apply_targets()
            self.w.world.step(render=False)
            self.w.update_attachments()
            self.w.update_conveyor()
            self.apply_camera_requests()
            self.w.update_chase_camera()
            self.w.update_policy_cameras()
            self.w.world.render()
            step += 1
            if step % STREAM_EVERY == 0:
                self.capture()

    def capture(self) -> None:
        frame = self.w.annotators[self.w.view].get_data()
        if frame is None or frame.size == 0:
            return
        buffer = io.BytesIO()
        Image.fromarray(frame[..., :3]).save(buffer, format="JPEG", quality=75)
        with self.frame_lock:
            self.frame = buffer.getvalue()
            self.frame_lock.notify_all()

    def stage_tote(self, sku: str, count: int, location_id: str) -> dict:
        teleport_base(self.w, self.w.approach_pose(location_id))
        for box in list(self.w.tote):
            self.w.tote.remove(box)
        stock = [b for b in self.w.boxes if b.sku == sku and b.location_id in self.w.locations
                 and self.w.locations[b.location_id]["kind"] == "storage"][:count]
        for slot, box in enumerate(stock):
            box.location_id = None
            box.deck_slot = slot
            surface, yaw = self.w.deck_slot(slot)
            box.rest_on(surface, yaw)
            self.w.set_dynamic(box, True)
            self.w.tote.append(box)
        return {"staged": len(stock), "at": location_id}

    def immediate(self, name: str, payload: dict) -> dict:
        if name == "reset":
            self.w.base_target = self.w.approach_pose("DOCK-1") if "DOCK-1" in self.w.locations else np.zeros(3)
            positions = self.w.robot.get_joint_positions()
            positions[self.w.base_idx] = self.w.base_target
            positions[self.w.arm_idx] = self.w.arm_home
            self.w.robot.set_joint_positions(positions)
            self.w.arm_target = self.w.arm_home.copy()
            return {"counts": self.w.reset_inventory(payload.get("missing", []), int(payload.get("seed", 0)),
                                                         payload.get("misplaced", []))}
        if name == "state":
            x, y, theta = self.w.base_pose()
            return {"x": float(x), "y": float(y), "theta": float(theta), "tote": [b.sku for b in self.w.tote],
                    "busy": not self.inbox.empty()}
        if name == "debug":
            return self.w.debug()
        if name == "counts":
            return {"counts": self.w.counts()}
        if name == "stage_tote":
            return self.stage_tote(payload.get("sku", "SKU-1002"), int(payload.get("count", 5)),
                                   payload.get("location_id", "DISPATCH-1"))
        if name == "probe":
            x, y, theta = self.w.base_pose()
            rotation = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
            results = []
            for local in payload["points"]:
                xy = np.array([x, y]) + rotation @ np.array(local[:2])
                yaw = theta + local[3]
                pitch = local[4] if len(local) > 4 else 0.0
                orientation = quat_to_rot_matrix(euler_angles_to_quat(np.array([0.0, math.pi / 2 + pitch, yaw])))
                try:
                    self.w.solve_ik(np.array([xy[0], xy[1], local[2]]), rot_matrix_to_quat(orientation))
                    results.append(True)
                except SimError:
                    results.append(False)
            return {"ok": results}
        if name == "camera":
            self.w.move_camera(payload)
            return {"view": self.w.view}
        if name == "view":
            if payload.get("view") in self.w.annotators:
                self.w.view = payload["view"]
            return {"view": self.w.view}
        raise ValueError(name)


def encode_jpeg(frame: np.ndarray, quality: int = 90) -> str:
    buffer = io.BytesIO()
    Image.fromarray(frame).save(buffer, format="JPEG", quality=quality)
    return base64.b64encode(buffer.getvalue()).decode()


def teleport_base(warehouse: Warehouse, pose: np.ndarray) -> None:
    positions = warehouse.robot.get_joint_positions()
    positions[warehouse.base_idx] = pose
    positions[warehouse.arm_idx] = warehouse.work_home
    positions[warehouse.finger_idx] = 0.04
    warehouse.robot.set_joint_positions(positions)
    warehouse.base_target = pose.copy()
    warehouse.arm_target = warehouse.work_home.copy()
    warehouse.finger_target = 0.04


class Watch:
    def __init__(self):
        self.frame = b""
        self.lock = threading.Condition()
        self.ticks = 0
        self.annotator = None

    def camera(self, warehouse: Warehouse) -> None:
        x, y, theta = warehouse.base_pose()
        turn = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
        eye = np.array([x, y]) + turn @ np.array([0.1, 1.9])
        target = np.array([x, y]) + turn @ np.array([0.7, 0.0])
        set_camera_view(eye=np.array([eye[0], eye[1], 1.5]), target=np.array([target[0], target[1], 0.45]),
                        camera_prim_path="/World/WatchCam")
        if self.annotator is None:
            UsdGeom.Camera(warehouse.stage.GetPrimAtPath("/World/WatchCam")).CreateFocalLengthAttr(14.0)
            self.annotator = rep.AnnotatorRegistry.get_annotator("rgb")
            self.annotator.attach([rep.create.render_product("/World/WatchCam", (640, 480))])

    def capture(self, warehouse: Warehouse) -> None:
        self.ticks += 1
        if self.ticks % STREAM_EVERY:
            return
        if self.annotator is None:
            UsdGeom.Camera.Define(warehouse.stage, "/World/WatchCam")
        self.camera(warehouse)
        frame = self.annotator.get_data()
        if frame is None or frame.size == 0:
            return
        buffer = io.BytesIO()
        Image.fromarray(frame[..., :3]).save(buffer, format="JPEG", quality=75)
        with self.lock:
            self.frame = buffer.getvalue()
            self.lock.notify_all()

    def serve(self) -> None:
        watch = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("content-type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    while True:
                        with watch.lock:
                            watch.lock.wait(timeout=1.0)
                            frame = watch.frame
                        if frame:
                            self.wfile.write(b"--frame\r\ncontent-type: image/jpeg\r\n\r\n" + frame + b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    return

        http = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()


WATCH = Watch() if args.watch or args.teleop else None


def step(warehouse: Warehouse) -> None:
    warehouse.apply_targets()
    warehouse.update_attachments()
    warehouse.update_policy_cameras()
    warehouse.world.step(render=True)
    if WATCH:
        WATCH.capture(warehouse)
    if TELEOP_VIEWS:
        TELEOP_VIEWS.capture(warehouse)


def instruction_variants(warehouse: Warehouse, sku: str) -> list[str]:
    product = warehouse.products[sku]
    noun = product["name"].split()[-1].lower()
    return [f"pick up the {product['name'].lower()}", f"pick up the {product['description']}",
            f"grab the {product['color']} {noun}"]


def run_recorded(warehouse: Warehouse, generator, frames: dict) -> None:
    ticks = 0
    try:
        while True:
            next(generator)
            step(warehouse)
            ticks += 1
            if ticks % CONTROL_EVERY == 0:
                for key, value in warehouse.policy_observation().items():
                    frames[key].append(value)
    except StopIteration:
        pass


class Teleop:
    def __init__(self):
        self.lock = threading.Lock()
        self.keys: set[str] = set()
        self.keys_at = 0.0
        self.commands: list[str] = []
        self.assist = False
        self.paused = False
        self.status: dict = {}
        self.head = self.wrist = b""
        self.views: dict[str, bytes] = {}

    def active_keys(self) -> set[str]:
        with self.lock:
            return set(self.keys) if time.monotonic() - self.keys_at < TELEOP_KEY_TIMEOUT else set()

    def set_keys(self, keys: list[str]) -> None:
        with self.lock:
            self.keys, self.keys_at = {k.lower() for k in keys}, time.monotonic()

    def push_command(self, name: str) -> None:
        with self.lock:
            if name == "assist":
                self.assist = not self.assist
            elif name == "pause":
                self.paused = not self.paused
            else:
                self.commands.append(name)

    def pop_command(self) -> str | None:
        with self.lock:
            return self.commands.pop(0) if self.commands else None

    def update(self, **values) -> None:
        with self.lock:
            self.status.update(values)

    def snapshot(self) -> dict:
        with self.lock:
            return {**self.status, "assist": self.assist, "paused": self.paused}

    def snapshot_frame(self, name: str) -> bytes:
        with self.lock:
            sources = {"wrist": self.wrist, "head": self.head, **self.views}
        return WATCH.frame if name == "overview" else sources.get(name, b"")

    def set_view(self, name: str, jpeg: bytes) -> None:
        with self.lock:
            self.views[name] = jpeg

    def set_cameras(self, head: np.ndarray, wrist: np.ndarray) -> None:
        head_jpeg, wrist_jpeg = base64.b64decode(encode_jpeg(head, 80)), base64.b64decode(encode_jpeg(wrist, 80))
        with self.lock:
            self.head, self.wrist = head_jpeg, wrist_jpeg

    def serve(self) -> None:
        teleop = self
        page = (Path(__file__).parent / "teleop.html").read_bytes()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _mjpeg(self, source) -> None:
                self.send_response(200)
                self.send_header("content-type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    while True:
                        frame = source()
                        if frame:
                            self.wfile.write(b"--frame\r\ncontent-type: image/jpeg\r\n\r\n" + frame + b"\r\n")
                        time.sleep(1 / 15)
                except (BrokenPipeError, ConnectionResetError):
                    return

            def do_GET(self):
                if self.path == "/":
                    self._send(200, page, "text/html")
                elif self.path == "/status":
                    self._send(200, json.dumps(teleop.snapshot()).encode(), "application/json")
                elif self.path == "/stream":
                    self._mjpeg(lambda: WATCH.frame)
                elif self.path == "/head":
                    self._mjpeg(lambda: teleop.head)
                elif self.path == "/wrist":
                    self._mjpeg(lambda: teleop.wrist)
                elif self.path.startswith("/view/"):
                    name = self.path.split("/")[-1]
                    self._mjpeg(lambda: teleop.views.get(name, b""))
                elif self.path.startswith("/snap/"):
                    name = self.path.split("/")[2].split("?")[0]
                    frame = teleop.snapshot_frame(name)
                    self._send(200 if frame else 204, frame, "image/jpeg")
                else:
                    self._send(404, b"", "text/plain")

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
                if self.path == "/keys":
                    teleop.set_keys(payload.get("keys", []))
                elif self.path == "/command":
                    teleop.push_command(payload.get("name", ""))
                self._send(200, b"{}", "application/json")

        http = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()


TELEOP = Teleop()


class TeleopViews:
    def __init__(self):
        self.annotators: dict = {}
        self.ticks = 0

    def pose(self, warehouse: Warehouse, name: str) -> tuple[np.ndarray, np.ndarray]:
        x, y, theta = warehouse.base_pose()
        turn = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
        local_eye, local_target = TELEOP_VIEW_POSES[name]
        eye, target = np.array([x, y]) + turn @ np.array(local_eye[:2]), np.array([x, y]) + turn @ np.array(local_target[:2])
        return np.array([eye[0], eye[1], local_eye[2]]), np.array([target[0], target[1], local_target[2]])

    def capture(self, warehouse: Warehouse) -> None:
        self.ticks += 1
        if self.ticks % STREAM_EVERY:
            return
        for name in TELEOP_VIEW_POSES:
            path = f"/World/Teleop{name.capitalize()}Cam"
            if name not in self.annotators:
                UsdGeom.Camera.Define(warehouse.stage, path).CreateFocalLengthAttr(TELEOP_VIEW_FOCAL)
            eye, target = self.pose(warehouse, name)
            set_camera_view(eye=eye, target=target, camera_prim_path=path)
            if name not in self.annotators:
                annotator = rep.AnnotatorRegistry.get_annotator("rgb")
                annotator.attach([rep.create.render_product(path, TELEOP_VIEW_SIZE)])
                self.annotators[name] = annotator
                continue
            frame = self.annotators[name].get_data()
            if frame is not None and frame.size:
                TELEOP.set_view(name, base64.b64decode(encode_jpeg(frame[..., :3], 80)))


TELEOP_VIEWS = TeleopViews() if args.teleop else None


def teleop_session(warehouse: Warehouse) -> None:
    rng = np.random.default_rng(args.seed)
    warehouse.rng = np.random.default_rng(args.seed + 1)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    commands = Commands(warehouse, pose_source="sim")
    commands.base_carry = False
    warehouse.push_encoding = True
    log = open(out / "teleop_log.jsonl", "a")
    saved = len(list(out.glob("episode_*.npz")))
    TELEOP.serve()
    print(f"TELEOP_READY port={args.port}", flush=True)
    attempt = 0
    while app.is_running():
        attempt += 1
        TELEOP.update(phase="loading", saved=saved, attempt=attempt, moved=0.0, frames=0)
        restock_blockers(warehouse, rng)
        for _ in range(SETTLE_STEPS // 2):
            step(warehouse)
        targets = [b for b in warehouse.boxes_at(DEEP_BIN) if warehouse.pickable(b.sku)]
        box = targets[rng.integers(len(targets))]
        blockers = commands.blockers(box)
        if not blockers:
            continue
        blocker = blockers[0]
        teleport_base(warehouse, warehouse.approach_pose(DEEP_BIN) + stance_jitter(rng))
        for _ in range(20):
            step(warehouse)
        target_before = warehouse.sync(box).copy()
        commands.teleop_result = "discard"
        try:
            run_silent(warehouse, commands.take_tool("paddle"))
            warehouse.marker = commands.true_target(blocker).center.copy()
            TELEOP.update(phase="aligning", blocker=BLOCKER_NAMES[blocker.ycb], yaw=round(blocker.yaw, 2))
            run_silent(warehouse, commands.push_aside(blocker, box, "teleop"))
        except SimError as err:
            TELEOP.update(phase="skipped", last=str(err))
            print(f"teleop scene skipped: {err}", flush=True)
        trace = getattr(commands, "push_trace", {}) or {}
        frames = getattr(commands, "teleop_frames", None)
        pose = commands.true_target(blocker)
        record = {"attempt": attempt, "blocker": blocker.ycb, "yaw": round(blocker.yaw, 2), "result": commands.teleop_result,
                  "moved": trace.get("final_lateral"), "goal": trace.get("goal_lateral"), "depth": trace.get("final_depth"),
                  "upright": bool(abs(float(pose.axes[2, int(np.argmax(np.abs(pose.axes[2, :])))])) > UPRIGHT_COS),
                  "target_moved": round(float(np.linalg.norm(warehouse.sync(box) - target_before)), 3),
                  "instruction": trace.get("instruction"), "assist": TELEOP.assist}
        warehouse.marker = None
        commands.recording = False
        warehouse.trail = None
        if commands.teleop_result == "save" and frames and len(frames["state"]) > 2:
            human = np.array(frames["human"])
            np.savez_compressed(out / f"episode_{saved:04d}.npz", instruction=record["instruction"], head=np.stack(frames["head"]),
                                wrist=np.stack(frames["wrist"]), state=np.stack(frames["state"]),
                                action=np.stack(frames["action"]).astype(np.float32), human=human)
            record.update(episode=saved, frames=len(human), human_frames=int(human.sum()))
            saved += 1
        log.write(json.dumps(record) + "\n")
        log.flush()
        TELEOP.update(last=record)
        print(f"teleop {record['result']} attempt {attempt}: {record}", flush=True)
        try:
            run_silent(warehouse, commands.return_tool())
        except SimError:
            pass
        warehouse.tool = None


def run_silent(warehouse: Warehouse, generator):
    try:
        while True:
            next(generator)
            step(warehouse)
    except StopIteration as stop:
        return stop.value


def stance_jitter(rng: np.random.Generator) -> np.ndarray:
    return np.array([rng.uniform(-STANCE_JITTER[0], 0.0), rng.uniform(-STANCE_JITTER[1], STANCE_JITTER[1]),
                     rng.uniform(-STANCE_JITTER[2], STANCE_JITTER[2])])


def scene_of(location_id: str) -> str:
    return {TOPPLE_BIN: "toppled", TOTE_BIN: "tote", DEEP_BIN: "deep"}.get(location_id, "shelf")


def snapshot_location(warehouse: Warehouse, location_id: str) -> list:
    return [(b, b.prim.get_world_pose()) for b in warehouse.boxes_at(location_id, background=True)]


def restore_location(warehouse: Warehouse, location_id: str, snapshot: list) -> None:
    for item, (position, orientation) in snapshot:
        warehouse.set_dynamic(item, False)
        item.location_id = location_id
        item.prim.set_world_pose(position, orientation)
        warehouse.set_dynamic(item, True)
    for _ in range(SETTLE_STEPS // 4):
        step(warehouse)


BLOCKER_POOL = ("002_master_chef_can", "010_potted_meat_can", "008_pudding_box", "009_gelatin_box", "061_foam_brick", "004_sugar_box")
BLOCKER_NAMES = {"002_master_chef_can": "coffee can", "010_potted_meat_can": "potted meat can", "008_pudding_box": "pudding box",
                 "009_gelatin_box": "gelatin box", "061_foam_brick": "foam brick", "004_sugar_box": "sugar box"}
PUSH_MAX_DEPTH_SHIFT = 0.03
TARGET_MAX_SHIFT = 0.01
READY_POSE_TOLERANCE = 0.02


def restock_blockers(warehouse: Warehouse, rng: np.random.Generator) -> None:
    items = warehouse.boxes_at(DEEP_BIN, background=True)
    for item in [b for b in items if not warehouse.pickable(b.sku)]:
        warehouse.stage.RemovePrim(item.path)
        warehouse.boxes.remove(item)
    targets = [b for b in warehouse.boxes_at(DEEP_BIN, background=True) if warehouse.pickable(b.sku)]
    blockers = []
    for _ in targets:
        blocker = warehouse.spawn_item(None, None, BLOCKER_POOL[rng.integers(len(BLOCKER_POOL))])
        blocker.location_id = DEEP_BIN
        blockers.append(blocker)
    warehouse.layout_deep(DEEP_BIN, targets + blockers)


def run_gated(warehouse: Warehouse, commands: "Commands", generator, frames: dict):
    ticks = 0
    try:
        while True:
            next(generator)
            step(warehouse)
            if commands.recording:
                ticks += 1
                if ticks % CONTROL_EVERY == 0:
                    for key, value in warehouse.policy_observation().items():
                        frames[key].append(value)
    except StopIteration as stop:
        return stop.value


def collect_push_demos(warehouse: Warehouse, episodes: int) -> None:
    from pathlib import Path

    rng = np.random.default_rng(args.seed)
    warehouse.rng = np.random.default_rng(args.seed + 1)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    commands = Commands(warehouse, pose_source="sim")
    commands.base_carry = False
    warehouse.push_encoding = True
    log = open(out / "collect_log.jsonl", "a")
    saved = len(list(out.glob("episode_*.npz")))
    attempt = 0
    while saved < episodes and attempt < episodes * 3:
        attempt += 1
        restock_blockers(warehouse, rng)
        for _ in range(SETTLE_STEPS // 2):
            step(warehouse)
        targets = [b for b in warehouse.boxes_at(DEEP_BIN) if warehouse.pickable(b.sku)]
        box = targets[rng.integers(len(targets))]
        blockers = commands.blockers(box)
        if not blockers:
            continue
        blocker = blockers[0]
        teleport_base(warehouse, warehouse.approach_pose(DEEP_BIN) + stance_jitter(rng))
        for _ in range(20):
            step(warehouse)
        frames = {"head": [], "wrist": [], "state": [], "finger_target": []}
        target_before = warehouse.sync(box).copy()
        record = {"attempt": attempt, "blocker": blocker.ycb, "yaw": round(blocker.yaw, 2)}
        try:
            run_silent(warehouse, commands.take_tool("paddle"))
            ready_error = float(np.abs(warehouse.robot.get_joint_positions()[warehouse.arm_idx] - warehouse.work_home).max())
            if ready_error > READY_POSE_TOLERANCE:
                raise SimError("ready", f"paddle pickup ended {ready_error:.3f} rad off the push ready pose")
            warehouse.marker = commands.true_target(blocker).center.copy()
            run_gated(warehouse, commands, commands.push_aside(blocker, box, args.push_policy), frames)
            trace = commands.push_trace
            for _ in range(30):
                step(warehouse)
            pose = commands.true_target(blocker)
            record.update(moved=trace["final_lateral"], goal=trace["goal_lateral"], depth=trace["final_depth"],
                          upright=bool(abs(float(pose.axes[2, int(np.argmax(np.abs(commands.true_target(blocker).axes[2, :])))])) > UPRIGHT_COS)
                          and trace["upright"],
                          target_moved=round(float(np.linalg.norm(warehouse.sync(box) - target_before)), 3))
            run_silent(warehouse, commands.return_tool())
        except SimError as err:
            record["skipped"] = str(err)
        commands.recording = False
        warehouse.marker = None
        warehouse.tool = None
        warehouse.trail = None
        ok = ("skipped" not in record and record["moved"] >= record["goal"] and record["upright"]
              and abs(record["depth"]) <= PUSH_MAX_DEPTH_SHIFT and record["target_moved"] < TARGET_MAX_SHIFT
              and len(frames["state"]) > 2)
        if args.push_policy == "vla" or args.push_eval:
            log.write(json.dumps({**record, "success": bool(ok), "frames": len(frames["state"]), "trace": commands.push_trace,
                                  "instruction": commands.push_trace.get("instruction") if "skipped" not in record else None}) + "\n")
            log.flush()
            if frames["state"]:
                np.savez_compressed(out / f"eval_{attempt:04d}.npz", head=np.stack(frames["head"]), wrist=np.stack(frames["wrist"]),
                                    state=np.stack(frames["state"]))
            saved += "skipped" not in record
            print(f"eval {saved}/{episodes} (attempt {attempt}) success={ok}", flush=True)
            continue
        if not ok:
            log.write(json.dumps({**record, "saved": False}) + "\n")
            log.flush()
            continue
        side = commands.push_trace["side"]
        direction = "left" if side > 0 else "right"
        name = BLOCKER_NAMES[blocker.ycb]
        instruction = (f"push the {name} to the {direction}", f"slide the {name} {direction} out of the way",
                       f"clear the {name} by pushing it {direction}")[rng.integers(3)]
        state = np.stack(frames["state"])
        pose_size = state.shape[1] - 1
        next_pose = np.concatenate([state[1:, :pose_size], state[-1:, :pose_size]])
        action = np.concatenate([next_pose, np.array(frames["finger_target"])[:, None]], axis=1).astype(np.float32)
        np.savez_compressed(out / f"episode_{saved:04d}.npz", instruction=instruction, head=np.stack(frames["head"]),
                            wrist=np.stack(frames["wrist"]), state=state, action=action)
        log.write(json.dumps({**record, "saved": True, "episode": saved, "frames": len(frames["state"]), "instruction": instruction}) + "\n")
        log.flush()
        saved += 1
        print(f"saved {saved}/{episodes} (attempt {attempt}) {instruction}", flush=True)


def collect_demos(warehouse: Warehouse, episodes: int) -> None:
    from pathlib import Path

    rng = np.random.default_rng(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    commands = Commands(warehouse, pose_source="sim")
    log = open(out / "collect_log.jsonl", "a")
    saved = len(list(out.glob("episode_*.npz")))
    attempt = 0
    while saved < episodes and attempt < episodes * 3:
        if attempt % RESHUFFLE_EVERY == 0:
            warehouse.reset_inventory([], int(rng.integers(1_000_000)))
            for _ in range(SETTLE_STEPS):
                step(warehouse)
        attempt += 1
        stocked = sorted({(b.location_id, b.sku) for b in warehouse.boxes if b.location_id and b.sku and warehouse.pickable(b.sku)
                          and warehouse.locations[b.location_id]["kind"] in ("storage", "staging")
                          and (b.location_id != TOTE_BIN or args.scenario == "hard")})
        scene = DEMO_SCENES[rng.integers(len(DEMO_SCENES))]
        pool = [pair for pair in stocked if scene_of(pair[0]) == scene] or stocked
        location_id, sku = pool[rng.integers(len(pool))]
        jitter = stance_jitter(rng)
        teleport_base(warehouse, warehouse.approach_pose(location_id) + jitter)
        for _ in range(20):
            step(warehouse)
        candidates = [b for b in warehouse.boxes_at(location_id) if b.sku == sku]
        box = candidates[rng.integers(len(candidates))]
        snapshot = snapshot_location(warehouse, location_id)
        start = warehouse.sync(box).copy()
        perturbation = None
        if rng.random() < args.perturb_rate:
            perturbation = ("nudge", "offset", "regrasp")[rng.integers(3)]
        frames = {"head": [], "wrist": [], "state": [], "finger_target": []}
        held = False
        try:
            run_silent(warehouse, commands.face_work())
            run_silent(warehouse, commands.align_to(commands.set_marker(box, rng)))
            warehouse.look_z = warehouse.surface_z(location_id) + HEAD_LOOK_ABOVE
            for _ in range(6):
                step(warehouse)
            run_recorded(warehouse, commands.expert_episode(box, perturbation, rng), frames)
            for i in range(3 * CONTROL_EVERY):
                step(warehouse)
                if i % CONTROL_EVERY == 0:
                    for key, value in warehouse.policy_observation().items():
                        frames[key].append(value)
            held = run_silent(warehouse, commands.lift(box, start[2]))
        except SimError as err:
            log.write(json.dumps({"attempt": attempt, "skipped": str(err)}) + "\n")
        warehouse.marker = None
        warehouse.held = None
        warehouse.finger_target = 0.04
        for _ in range(20):
            step(warehouse)
        restore_location(warehouse, location_id, snapshot)
        if not held or len(frames["state"]) < 2:
            log.write(json.dumps({"attempt": attempt, "skipped": "grasp not held", "scene": scene_of(location_id)}) + "\n")
            continue
        instruction = instruction_variants(warehouse, sku)[rng.integers(3)]
        state = np.stack(frames["state"])
        state[:, 5] = np.unwrap(state[:, 5])
        next_pose = np.concatenate([state[1:, :6], state[-1:, :6]])
        action = np.concatenate([next_pose, np.array(frames["finger_target"])[:, None]], axis=1).astype(np.float32)
        np.savez_compressed(
            out / f"episode_{saved:04d}.npz", instruction=instruction, head=np.stack(frames["head"]),
            wrist=np.stack(frames["wrist"]), state=state, action=action,
        )
        log.write(json.dumps({"episode": saved, "location_id": location_id, "scene": scene_of(location_id), "sku": sku,
                              "perturbation": perturbation, "pushed": commands.last_grasp.get("pushed"),
                              "frames": len(frames["state"]), "instruction": instruction}) + "\n")
        log.flush()
        saved += 1
        print(f"saved {saved}/{episodes} (attempt {attempt})", flush=True)


def run_trial(warehouse: Warehouse, commands: "Commands", pose: np.ndarray, box: Item, policy: str) -> dict:
    teleport_base(warehouse, pose)
    if box.location_id == DEEP_BIN and args.scenario == "hard":
        warehouse.layout_deep(DEEP_BIN, warehouse.boxes_at(DEEP_BIN, background=True))
        for _ in range(SETTLE_STEPS // 2):
            step(warehouse)
    for _ in range(20):
        step(warehouse)
    start = warehouse.sync(box).copy()
    location_id = box.location_id
    snapshot = snapshot_location(warehouse, location_id)

    def grasp_and_lift():
        commands.pose_source = "sim" if policy == "expert" else args.pose
        if policy == "vla":
            yield from commands.grasp_vla(box, instruction_for(warehouse, box.sku))
        else:
            yield from commands.grasp_scripted(box)
        return (yield from commands.lift(box, start[2]))

    generator = grasp_and_lift()
    ticks, result = 0, False
    try:
        while True:
            next(generator)
            step(warehouse)
            ticks += 1
    except StopIteration as stop:
        result = bool(stop.value)
    except SimError as err:
        result = False
        commands.last_grasp = {**getattr(commands, "last_grasp", {}), "error": str(err)}
    miss = float(np.linalg.norm(warehouse.fingertip() - warehouse.sync(box)))
    warehouse.held = None
    warehouse.tool = None
    warehouse.trail = None
    warehouse.finger_target = 0.04
    for _ in range(30):
        step(warehouse)
    restore_location(warehouse, location_id, snapshot)
    return {"success": result, "sim_seconds": ticks * PHYSICS_DT, "miss_m": miss, **getattr(commands, "last_grasp", {})}


def eval_grasps(warehouse: Warehouse, trials: int) -> None:
    from pathlib import Path

    rng = np.random.default_rng(args.seed)
    commands = Commands(warehouse)
    for _ in range(SETTLE_STEPS):
        step(warehouse)
    scenes = {"toppled": {TOPPLE_BIN}, "tote": {TOTE_BIN}, "deep": {DEEP_BIN}, "hard": {TOPPLE_BIN, TOTE_BIN, DEEP_BIN}}
    allowed = scenes.get(args.eval_scene)
    stocked = sorted({(b.location_id, b.sku) for b in warehouse.boxes if b.location_id and b.sku and warehouse.pickable(b.sku)
                      and (b.location_id in allowed if allowed else warehouse.locations[b.location_id]["kind"] == "storage")})
    rows = []
    for trial in range(trials):
        location_id, sku = stocked[rng.integers(len(stocked))]
        jitter = stance_jitter(rng)
        pose = warehouse.approach_pose(location_id) + jitter
        candidates = [b for b in warehouse.boxes_at(location_id) if b.sku == sku]
        box = candidates[rng.integers(len(candidates))]
        row = {"trial": trial, "location_id": location_id, "sku": sku, "scene": next((k for k, v in scenes.items() if k != "hard" and location_id in v), "nominal")}
        for policy in args.policies:
            row[policy] = run_trial(warehouse, commands, pose, box, policy)
        rows.append(row)
        print(json.dumps(row), flush=True)
    summary = {"trials": trials}
    for policy in args.policies:
        summary[f"{policy}_success"] = sum(r[policy]["success"] for r in rows) / trials
        summary[f"{policy}_mean_s"] = float(np.mean([r[policy]["sim_seconds"] for r in rows]))
        for sku in sorted({r["sku"] for r in rows}):
            subset = [r for r in rows if r["sku"] == sku]
            summary[f"{policy}_success_{sku}"] = sum(r[policy]["success"] for r in subset) / len(subset)
        for scene in sorted({r["scene"] for r in rows}):
            subset = [r for r in rows if r["scene"] == scene]
            summary[f"{policy}_success_{scene}"] = sum(r[policy]["success"] for r in subset) / len(subset)
    if {"scripted", "vla"} <= set(args.policies):
        summary["vla_or_fallback_success"] = sum(r["vla"]["success"] or r["scripted"]["success"] for r in rows) / trials
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "grasp_eval.json").write_text(json.dumps({"summary": summary, "trials": rows}, indent=2))


def make_handler(server: Server):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _json(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("access-control-allow-origin", "*")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith("/stream.mjpg"):
                self.send_response(200)
                self.send_header("content-type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("access-control-allow-origin", "*")
                self.end_headers()
                try:
                    while True:
                        with server.frame_lock:
                            server.frame_lock.wait(timeout=1.0)
                            frame = server.frame
                        if frame:
                            self.wfile.write(b"--frame\r\ncontent-type: image/jpeg\r\n\r\n" + frame + b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    return
            elif self.path.startswith("/frame.jpg"):
                self.send_response(200)
                self.send_header("content-type", "image/jpeg")
                self.end_headers()
                self.wfile.write(server.frame)
            elif self.path.startswith("/state"):
                self._json(*server.submit("state", {}))
            elif self.path.startswith("/debug"):
                self._json(*server.submit("debug", {}))
            elif self.path.startswith("/counts"):
                self._json(*server.submit("counts", {}))
            elif self.path.startswith("/health"):
                self._json(200, {"status": "ok"})
            else:
                self._json(404, {"error": "not_found"})

        def do_POST(self):
            length = int(self.headers.get("content-length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            name = self.path.strip("/").split("?")[0]
            if name not in ("navigate", "inspect", "pick", "place", "stow", "reset", "view", "camera", "release", "resolve", "probe",
                            "take_tool", "return_tool", "push_blocker", "stage_tote"):
                return self._json(404, {"error": "unknown_command"})
            if name in ("view", "camera"):
                server.queue_camera(name, payload)
                return self._json(200, {"queued": name})
            self._json(*server.submit(name, payload))

    return Handler


def main() -> None:
    warehouse = Warehouse(streaming=not (args.collect or args.collect_push or args.eval_grasp))
    warehouse.reset_inventory([], args.seed)
    if args.teleop:
        teleop_session(warehouse)
        app.close()
        return
    if args.collect_push:
        if WATCH:
            WATCH.serve()
        collect_push_demos(warehouse, args.collect_push)
        app.close()
        return
    if args.collect:
        collect_demos(warehouse, args.collect)
        app.close()
        return
    if WATCH:
        WATCH.serve()
    if args.eval_grasp:
        eval_grasps(warehouse, args.eval_grasp)
        app.close()
        return
    server = Server(warehouse)
    http = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(server))
    threading.Thread(target=http.serve_forever, daemon=True).start()
    print(f"WAREHOUSE_SIM_READY port={args.port}", flush=True)
    server.run()
    app.close()


if __name__ == "__main__":
    main()
