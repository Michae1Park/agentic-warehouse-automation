import argparse
import base64
import io
import json
import math
import queue
import threading
import time
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser()
parser.add_argument("--api", default="http://localhost:8010")
parser.add_argument("--port", type=int, default=8020)
parser.add_argument("--width", type=int, default=1280)
parser.add_argument("--height", type=int, default=720)
args, _ = parser.parse_known_args()

from isaacsim import SimulationApp

app = SimulationApp({"headless": True, "width": args.width, "height": args.height})

import numpy as np
import omni.replicator.core as rep
from isaacsim.core.api import World
from isaacsim.core.api.objects import FixedCuboid, VisualCuboid
from isaacsim.core.prims import SingleArticulation, SingleXFormPrim
from isaacsim.core.utils.rotations import euler_angles_to_quat, quat_to_rot_matrix
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
from pxr import Gf, UsdGeom, UsdLux, UsdPhysics

PHYSICS_DT = 1 / 60
BASE_SPEED = 1.0
BASE_TURN_SPEED = 1.6
BOX_SIZE = np.array([0.10, 0.12, 0.08])
SHELF_DEPTH = 0.6
SHELF_BOARD = 0.04
BIN_LENGTH = 1.3
APPROACH_OFFSET = 1.0
IK_ATTEMPTS = 8
SCAN_STANDOFF = 0.8
CORRIDOR_Y = -0.6
TOTE_OFFSET = np.array([-0.3, 0.0, 0.46])
FRONT_GRASP = euler_angles_to_quat(np.array([0.0, math.pi / 2, 0.0]))
STREAM_EVERY = 3
INSPECT_RES = (896, 384)

COLORS = {
    "red": (0.7, 0.0, 0.0), "blue": (0.0, 0.15, 0.85), "black": (0.03, 0.03, 0.03), "white": (0.9, 0.9, 0.9),
    "yellow": (0.95, 0.88, 0.05), "green": (0.0, 0.5, 0.05), "orange": (1.0, 0.28, 0.0), "purple": (0.22, 0.0, 0.5),
    "cyan": (0.0, 0.75, 0.8), "magenta": (1.0, 0.0, 0.45), "gray": (0.62, 0.64, 0.68), "brown": (0.35, 0.18, 0.05),
}


def api_get(path: str):
    with urllib.request.urlopen(args.api + path, timeout=10) as response:
        return json.loads(response.read())


class SimError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Box:
    def __init__(self, prim: VisualCuboid, sku: str):
        self.prim = prim
        self.sku = sku
        self.location_id: str | None = None

    @property
    def path(self) -> str:
        return self.prim.prim_path

    def move(self, position, orientation=None) -> None:
        self.prim.set_world_pose(np.asarray(position, dtype=float), orientation)


class Warehouse:
    def __init__(self):
        self.world = World(stage_units_in_meters=1.0, physics_dt=PHYSICS_DT, rendering_dt=PHYSICS_DT)
        self.stage = get_current_stage()
        self.locations = {l["id"]: l for l in api_get("/api/locations")}
        self.colors = {r["sku"]: r["color"] for r in api_get("/api/products")}
        self.boxes: list[Box] = []
        self.box_counter = 0
        self.held: Box | None = None
        self.held_offset = np.zeros(3)
        self.tote: list[Box] = []
        self.grasp_failure_next = False
        self._build_environment()
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
            if location["kind"] in ("dispatch", "staging"):
                FixedCuboid(
                    f"/World/Pallets/{location['id'].replace('-', '_')}",
                    position=np.array([float(location["x"]) + 0.35, float(location["y"]), 0.07]),
                    scale=np.array([0.6, 0.9, 0.14]), color=np.array([0.6, 0.45, 0.28]),
                )

    def _build_robot(self) -> None:
        root = get_assets_root_path()
        add_reference_to_stage(root + "/Isaac/Robots/Clearpath/RidgebackFranka/ridgeback_franka.usd", "/World/Robot")
        anchor = UsdPhysics.FixedJoint.Define(self.stage, "/World/Robot/world_anchor")
        anchor.CreateBody1Rel().SetTargets(["/World/Robot/world"])
        self.robot = self.world.scene.add(SingleArticulation("/World/Robot", name="robot"))
        self.tote_prim = VisualCuboid(
            "/World/Tote", position=np.array([0.0, 0.0, 0.3]), scale=np.array([0.3, 0.44, 0.04]),
            color=np.array([0.2, 0.3, 0.7]),
        )

    def _init_robot(self) -> None:
        names = self.robot.dof_names
        self.base_idx = np.array([names.index(n) for n in (
            "dummy_base_prismatic_x_joint", "dummy_base_prismatic_y_joint", "dummy_base_revolute_z_joint")])
        self.arm_idx = np.array([names.index(f"panda_joint{i}") for i in range(1, 8)])
        self.finger_idx = np.array([names.index("panda_finger_joint1"), names.index("panda_finger_joint2")])
        self.arm_home = np.array([0.0, -0.6, 0.0, -2.4, 0.0, 1.9, 0.785])
        config = interface_config_loader.load_supported_lula_kinematics_solver_config("Franka")
        self.ik = LulaKinematicsSolver(**config)
        self.ik_articulation = ArticulationKinematicsSolver(self.robot, self.ik, "right_gripper")
        self.link0 = SingleXFormPrim("/World/Robot/panda_link0")
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

    def _build_cameras(self) -> None:
        overview = UsdGeom.Camera.Define(self.stage, "/World/OverviewCam")
        overview.CreateFocalLengthAttr(18.0)
        set_camera_view(eye=np.array([-4.0, -7.5, 9.5]), target=np.array([6.0, 3.0, 0.0]), camera_prim_path="/World/OverviewCam")
        chase = UsdGeom.Camera.Define(self.stage, "/World/ChaseCam")
        chase.CreateFocalLengthAttr(16.0)
        inspect = UsdGeom.Camera.Define(self.stage, "/World/InspectCam")
        inspect.CreateFocalLengthAttr(11.0)
        inspect.CreateHorizontalApertureAttr(20.955)
        inspect.CreateVerticalApertureAttr(20.955 * INSPECT_RES[1] / INSPECT_RES[0])
        self.view = "overview"
        self.products_rp = {
            "overview": rep.create.render_product("/World/OverviewCam", (args.width, args.height)),
            "chase": rep.create.render_product("/World/ChaseCam", (args.width, args.height)),
            "inspect": rep.create.render_product("/World/InspectCam", INSPECT_RES),
        }
        self.annotators = {}
        for name, rp in self.products_rp.items():
            annotator = rep.AnnotatorRegistry.get_annotator("rgb")
            annotator.attach([rp])
            self.annotators[name] = annotator

    def grab(self, box: Box) -> None:
        hand_position, hand_orientation = self.hand.get_world_pose()
        box_position, _ = box.prim.get_world_pose()
        self.held_offset = quat_to_rot_matrix(hand_orientation).T @ (box_position - hand_position)
        self.held = box

    def debug(self) -> dict:
        from pxr import Usd
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
        bound = cache.ComputeWorldBound(self.stage.GetPrimAtPath("/World/Robot")).ComputeAlignedRange()
        return {
            "robot_bbox": [list(bound.GetMin()), list(bound.GetMax())],
            "base_link": [v.tolist() for v in SingleXFormPrim("/World/Robot/base_link").get_world_pose()],
            "link0": [v.tolist() for v in self.link0.get_world_pose()],
            "hand": [v.tolist() for v in self.hand.get_world_pose()],
            "joints": dict(zip(self.robot.dof_names, self.robot.get_joint_positions().tolist())),
        }

    def base_pose(self) -> np.ndarray:
        return self.robot.get_joint_positions()[self.base_idx]

    def apply_targets(self) -> None:
        positions = np.concatenate([self.base_target, self.arm_target, [self.finger_target, self.finger_target]])
        indices = np.concatenate([self.base_idx, self.arm_idx, self.finger_idx])
        self.robot.apply_action(ArticulationAction(joint_positions=positions, joint_indices=indices))

    def update_attachments(self) -> None:
        if self.held:
            position, orientation = self.hand.get_world_pose()
            self.held.move(position + quat_to_rot_matrix(orientation) @ self.held_offset)
        x, y, theta = self.base_pose()
        rotation = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
        tote_xy = np.array([x, y]) + rotation @ TOTE_OFFSET[:2]
        self.tote_prim.set_world_pose(np.array([tote_xy[0], tote_xy[1], TOTE_OFFSET[2] - BOX_SIZE[2] / 2 - 0.02]),
                                      euler_angles_to_quat(np.array([0, 0, theta])))
        for i, box in enumerate(self.tote):
            slot = np.array([(i % 2) * 0.12 - 0.06, (i // 2 % 3) * 0.14 - 0.14])
            xy = tote_xy + rotation @ slot
            box.move(np.array([xy[0], xy[1], TOTE_OFFSET[2] + (i // 6) * BOX_SIZE[2]]),
                     euler_angles_to_quat(np.array([0, 0, theta])))

    def update_chase_camera(self) -> None:
        x, y, theta = self.base_pose()
        eye = np.array([x - 2.6 * math.cos(theta), y - 2.6 * math.sin(theta) - 1.2, 2.4])
        set_camera_view(eye=eye, target=np.array([x + 0.6 * math.cos(theta), y + 0.6 * math.sin(theta), 0.6]),
                        camera_prim_path="/World/ChaseCam")

    def spawn_box(self, sku: str, location_id: str, position: np.ndarray) -> Box:
        self.box_counter += 1
        rgb = COLORS.get(self.colors.get(sku, "gray"), (0.5, 0.5, 0.5))
        prim = VisualCuboid(f"/World/Boxes/box_{self.box_counter}", position=position, scale=BOX_SIZE, color=np.array(rgb))
        box = Box(prim, sku)
        box.location_id = location_id
        self.boxes.append(box)
        return box

    def slot_positions(self, location_id: str, count: int) -> list[np.ndarray]:
        location = self.locations[location_id]
        x, y, z = float(location["x"]), float(location["y"]), float(location["z"])
        if location["kind"] in ("dispatch", "staging"):
            x, z = x + 0.35, 0.14
            columns, rows, spacing_y = 4, 6, 0.15
            y0 = y - (columns - 1) * spacing_y / 2
            return [np.array([x - 0.2 + (i // columns % 3) * 0.14, y0 + (i % columns) * spacing_y,
                              z + BOX_SIZE[2] / 2 + (i // (columns * 3)) * BOX_SIZE[2] + 0.005]) for i in range(count)]
        columns = 7
        spacing_y = (BIN_LENGTH - 0.2) / (columns - 1)
        y0 = y - (BIN_LENGTH - 0.2) / 2
        front_x = x - SHELF_DEPTH / 2 + BOX_SIZE[0] / 2 + 0.03
        return [np.array([front_x + (i // columns) * (BOX_SIZE[0] + 0.03), y0 + (i % columns) * spacing_y,
                          z + BOX_SIZE[2] / 2 + 0.005]) for i in range(count)]

    def boxes_at(self, location_id: str) -> list[Box]:
        return [b for b in self.boxes if b.location_id == location_id]

    def repack(self, location_id: str) -> None:
        boxes = sorted(self.boxes_at(location_id), key=lambda b: (b.sku, b.path))
        for box, position in zip(boxes, self.slot_positions(location_id, len(boxes))):
            box.move(position, np.array([1.0, 0, 0, 0]))

    def reset_inventory(self, missing: list[dict]) -> dict:
        for box in self.boxes:
            self.stage.RemovePrim(box.path)
        self.boxes, self.tote, self.held = [], [], None
        skip = {(m["location_id"], m["sku"]) for m in missing}
        by_location: dict[str, list[tuple[str, int]]] = {}
        for row in api_get("/api/inventory/locations"):
            if (row["location_id"], row["sku"]) not in skip and row["quantity"] > 0:
                by_location.setdefault(row["location_id"], []).append((row["sku"], row["quantity"]))
        for location_id, items in by_location.items():
            skus = [sku for sku, qty in items for _ in range(qty)]
            for sku, position in zip(skus, self.slot_positions(location_id, len(skus))):
                self.spawn_box(sku, location_id, position)
        return self.counts()

    def counts(self) -> dict:
        result: dict[str, dict[str, int]] = {}
        for box in self.boxes:
            if box.location_id:
                result.setdefault(box.location_id, {}).setdefault(box.sku, 0)
                result[box.location_id][box.sku] += 1
        return result

    def approach_pose(self, location_id: str) -> np.ndarray:
        location = self.locations[location_id]
        x, y = float(location["x"]), float(location["y"])
        if location["kind"] == "storage":
            return np.array([x - APPROACH_OFFSET, y, 0.0])
        if location["kind"] in ("dispatch", "staging"):
            return np.array([x - 0.6, y, 0.0])
        return np.array([x, y, 0.0])

    def solve_ik(self, position: np.ndarray, orientation: np.ndarray = FRONT_GRASP) -> np.ndarray:
        base_position, base_orientation = self.link0.get_world_pose()
        self.ik.set_robot_base_pose(base_position, base_orientation)
        for _ in range(IK_ATTEMPTS):
            action, success = self.ik_articulation.compute_inverse_kinematics(position, orientation)
            if success:
                return np.array(action.joint_positions)
        raise SimError("ik_failed", f"no arm solution for target {np.round(position, 2).tolist()}")


def wrap_angle(angle: float) -> float:
    return (angle + math.pi) % (2 * math.pi) - math.pi


class Commands:
    def __init__(self, warehouse: Warehouse):
        self.w = warehouse

    def drive_to(self, target: np.ndarray):
        while True:
            current = self.w.base_target.copy()
            delta = target[:2] - current[:2]
            distance = float(np.linalg.norm(delta))
            heading_error = wrap_angle(target[2] - current[2])
            if distance < 1e-3 and abs(heading_error) < 1e-3:
                break
            step = min(distance, BASE_SPEED * PHYSICS_DT)
            if distance > 0:
                current[:2] += delta / distance * step
            current[2] += float(np.clip(heading_error, -BASE_TURN_SPEED * PHYSICS_DT, BASE_TURN_SPEED * PHYSICS_DT))
            self.w.base_target = current
            yield
        for _ in range(20):
            yield

    def waypoints(self, goal: np.ndarray) -> list[np.ndarray]:
        x, y, _ = self.w.base_target
        points = []
        if abs(x - goal[0]) > 0.05:
            points.append(np.array([x, CORRIDOR_Y, 0.0]))
            points.append(np.array([goal[0], CORRIDOR_Y, 0.0]))
        points.append(np.array([goal[0], goal[1], 0.0]))
        return points

    def navigate(self, location_id: str):
        goal = self.w.approach_pose(location_id)
        yield from self.move_arm(self.w.arm_home, 40)
        for point in self.waypoints(goal):
            heading = math.atan2(point[1] - self.w.base_target[1], point[0] - self.w.base_target[0])
            if np.linalg.norm(point[:2] - self.w.base_target[:2]) > 0.05:
                yield from self.drive_to(np.array([self.w.base_target[0], self.w.base_target[1], heading]))
                yield from self.drive_to(np.array([point[0], point[1], heading]))
        yield from self.drive_to(goal)
        x, y, theta = self.w.base_pose()
        return {"x": float(x), "y": float(y), "theta": float(theta)}

    def move_arm(self, target: np.ndarray, steps: int = 90):
        start = self.w.arm_target.copy()
        for i in range(1, steps + 1):
            t = 0.5 - 0.5 * math.cos(math.pi * i / steps)
            self.w.arm_target = start + (target - start) * t
            yield
        for _ in range(10):
            yield

    def gripper(self, opening: float):
        self.w.finger_target = opening
        for _ in range(25):
            yield

    def reach(self, position: np.ndarray, steps: int = 90):
        joints = self.w.solve_ik(position)
        yield from self.move_arm(joints, steps)

    def inspect(self, location_id: str, distance: float = 1.3, height: float = 0.25, focal: float = 16.0):
        location = self.w.locations[location_id]
        x, y, z = float(location["x"]), float(location["y"]), float(location["z"])
        approach = self.w.approach_pose(location_id)
        at_bin = np.linalg.norm(self.w.base_target[:2] - approach[:2]) < 0.3
        if at_bin:
            yield from self.drive_to(approach - np.array([SCAN_STANDOFF, 0.0, 0.0]))
        UsdGeom.Camera(self.w.stage.GetPrimAtPath("/World/InspectCam")).GetFocalLengthAttr().Set(focal)
        set_camera_view(eye=np.array([x - distance, y, z + height]), target=np.array([x, y, z + 0.06]),
                        camera_prim_path="/World/InspectCam")
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

    def pick(self, sku: str, location_id: str, fail: bool = False):
        candidates = [b for b in self.w.boxes_at(location_id) if b.sku == sku]
        if not candidates:
            raise SimError("item_not_found", f"no {sku} at {location_id}")
        base = self.w.base_pose()
        box = min(candidates, key=lambda b: np.linalg.norm(b.prim.get_world_pose()[0][:2] - base[:2]))
        position, _ = box.prim.get_world_pose()
        yield from self.gripper(0.04)
        yield from self.reach(position + np.array([-0.16, 0, 0.02]))
        yield from self.reach(position + np.array([-0.02, 0, 0.0]), 50)
        yield from self.gripper(0.0)
        self.w.grab(box)
        box.location_id = None
        yield from self.reach(position + np.array([-0.06, 0, 0.04]), 30)
        yield from self.reach(position + np.array([-0.22, 0, 0.04]), 50)
        if fail:
            self.w.held = None
            box.location_id = location_id
            yield from self.gripper(0.04)
            for _ in range(60):
                yield
            self.w.repack(location_id)
            yield from self.move_arm(self.w.arm_home, 60)
            raise SimError("grasp_failed", f"{sku} slipped from gripper at {location_id}")
        yield from self.move_arm(self.w.arm_home, 70)
        self.w.held = None
        self.w.tote.append(box)
        yield from self.gripper(0.04)
        self.w.repack(location_id)
        return {"picked": sku, "tote": [b.sku for b in self.w.tote]}

    def place(self, location_id: str):
        location = self.w.locations[location_id]
        drop = np.array([float(location["x"]) - 0.05, float(location["y"]), 0.45])
        yield from self.reach(drop, 80)
        placed = []
        existing = len(self.w.boxes_at(location_id))
        for i, (box, position) in enumerate(zip(list(self.w.tote), self.w.slot_positions(location_id, existing + len(self.w.tote))[existing:])):
            box.move(position + np.array([0, 0, 0.05]), np.array([1.0, 0, 0, 0]))
            box.location_id = location_id
            placed.append(box.sku)
            for _ in range(6):
                yield
        self.w.tote = []
        yield from self.move_arm(self.w.arm_home, 60)
        return {"placed": placed}

    def stow(self, sku: str, location_id: str, fail: bool = False):
        box = next((b for b in self.w.tote if b.sku == sku), None)
        if not box:
            raise SimError("item_not_found", f"{sku} not in tote")
        location = self.w.locations[location_id]
        target = self.w.slot_positions(location_id, len(self.w.boxes_at(location_id)) + 1)[-1]
        self.w.tote.remove(box)
        self.w.grab(box)
        yield from self.reach(np.array([target[0] - 0.12, target[1], target[2] + 0.03]), 80)
        if fail:
            self.w.held = None
            self.w.tote.append(box)
            yield from self.move_arm(self.w.arm_home, 60)
            raise SimError("grasp_failed", f"stow of {sku} at {location_id} failed")
        self.w.held = None
        box.move(target + np.array([0, 0, 0.02]), np.array([1.0, 0, 0, 0]))
        box.location_id = location_id
        yield from self.gripper(0.04)
        yield from self.move_arm(self.w.arm_home, 60)
        self.w.repack(location_id)
        return {"stowed": sku}


class Server:
    def __init__(self, warehouse: Warehouse):
        self.w = warehouse
        self.commands = Commands(warehouse)
        self.inbox: queue.Queue = queue.Queue()
        self.frame: bytes = b""
        self.frame_lock = threading.Condition()

    def submit(self, name: str, payload: dict, timeout: float = 600) -> tuple[int, dict]:
        done = threading.Event()
        holder: dict = {}
        self.inbox.put((name, payload, done, holder))
        if not done.wait(timeout):
            return 504, {"error": "timeout"}
        return holder["status"], holder["body"]

    def run(self) -> None:
        current = None
        step = 0
        while app.is_running():
            if current is None and not self.inbox.empty():
                name, payload, done, holder = self.inbox.get()
                try:
                    if name in ("reset", "state", "counts", "view", "debug"):
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
            self.w.update_attachments()
            self.w.update_chase_camera()
            self.w.world.step(render=True)
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

    def immediate(self, name: str, payload: dict) -> dict:
        if name == "reset":
            self.w.base_target = self.w.approach_pose("DOCK-1") if "DOCK-1" in self.w.locations else np.zeros(3)
            positions = self.w.robot.get_joint_positions()
            positions[self.w.base_idx] = self.w.base_target
            positions[self.w.arm_idx] = self.w.arm_home
            self.w.robot.set_joint_positions(positions)
            self.w.arm_target = self.w.arm_home.copy()
            return {"counts": self.w.reset_inventory(payload.get("missing", []))}
        if name == "state":
            x, y, theta = self.w.base_pose()
            return {"x": float(x), "y": float(y), "theta": float(theta), "tote": [b.sku for b in self.w.tote],
                    "busy": not self.inbox.empty()}
        if name == "debug":
            return self.w.debug()
        if name == "counts":
            return {"counts": self.w.counts()}
        if name == "view":
            if payload.get("view") in self.w.annotators:
                self.w.view = payload["view"]
            return {"view": self.w.view}
        raise ValueError(name)


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
            if name not in ("navigate", "inspect", "pick", "place", "stow", "reset", "view"):
                return self._json(404, {"error": "unknown_command"})
            self._json(*server.submit(name, payload))

    return Handler


def main() -> None:
    warehouse = Warehouse()
    warehouse.reset_inventory([])
    server = Server(warehouse)
    http = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(server))
    threading.Thread(target=http.serve_forever, daemon=True).start()
    print(f"WAREHOUSE_SIM_READY port={args.port}", flush=True)
    server.run()
    app.close()


if __name__ == "__main__":
    main()
