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
parser.add_argument("--collect", type=int, default=0, help="record N scripted grasp demonstrations and exit")
parser.add_argument("--out", default="/workspace/vla/data/raw")
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--policy-url", default="http://localhost:8030")
parser.add_argument("--livestream", action="store_true", help="serve the viewport to the Isaac Sim WebRTC Streaming Client")
parser.add_argument("--graphics", choices=("basic", "high"), default="basic")
parser.add_argument("--eval-grasp", type=int, default=0, help="compare scripted vs VLA grasps on N randomized trials")
args, _ = parser.parse_known_args()

from isaacsim import SimulationApp

app = SimulationApp({"headless": True, "width": args.width, "height": args.height})
if args.livestream:
    from isaacsim.core.utils.extensions import enable_extension

    app.set_setting("/app/window/drawMouse", True)
    enable_extension("omni.kit.livestream.webrtc")
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
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade

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
WAREHOUSE_PROPS = "/Isaac/Environments/Simple_Warehouse/Props"
CARDBOX_SIZE = np.array([0.38, 0.25, 0.149])
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
ROLLER_TOP = 0.769
TOTE_X = 12.12
TOTE_SCALE = np.array([1.8, 1.8, 1.2])
TOTE_SIZE = np.array([0.198, 0.296, 0.146]) * TOTE_SCALE
TOTE_SPEED = 0.6
DISPATCH_STANDOFF = 0.6
RELEASE_POSES = [euler_angles_to_quat(np.array([0.0, pitch, 0.0])) for pitch in (math.pi * 0.95, math.pi * 0.75, math.pi / 2)]
POLICY_RES = (256, 256)
CONTROL_EVERY = 6
FINGERTIP_OFFSET = np.array([0.0, 0.0, 0.1034])
GRASP_TOLERANCE = 0.05
VLA_MAX_STEPS = 120
VLA_EXECUTE_ACTIONS = 5

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
        self.position = np.zeros(3)

    @property
    def path(self) -> str:
        return self.prim.prim_path

    def move(self, position, orientation=None) -> None:
        self.position = np.asarray(position, dtype=float)
        self.prim.set_world_pose(self.position, orientation)


class Warehouse:
    def __init__(self, streaming: bool = True):
        self.streaming = streaming
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
        self._build_conveyor()
        if args.graphics == "high":
            self._build_high_quality_environment()
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
            if location["kind"] == "staging":
                FixedCuboid(
                    f"/World/Pallets/{location['id'].replace('-', '_')}",
                    position=np.array([float(location["x"]) + 0.35, float(location["y"]), 0.07]),
                    scale=np.array([0.6, 0.9, 0.14]), color=np.array([0.6, 0.45, 0.28]),
                )

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
        for aisle in self.stage.GetPrimAtPath("/World/Shelves").GetChildren():
            for part in aisle.GetChildren():
                name = part.GetName()
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
        self.stage.GetPrimAtPath("/World/DomeLight").GetAttribute("inputs:intensity").Set(250.0)
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
            self._place_prop(f"/World/Conveyor/segment_{i}", (CONVEYOR_X, CONVEYOR_START_Y + i * CONVEYOR_SEGMENT, 0), -90, (1, 1, 1))
        end_y = CONVEYOR_START_Y + CONVEYOR_SEGMENTS * CONVEYOR_SEGMENT
        outbound = (CONVEYOR_X, end_y + 0.75, 0.0)
        self._add_prop("SM_PaletteA_01", "/World/Outbound/pallet", outbound, 0, (1, 1, 1))
        for i, (dx, dy, dz, asset) in enumerate([(-0.3, -0.25, 0, "SM_CardBoxB_01"), (0.25, -0.25, 0, "SM_CardBoxA_01"),
                                                  (-0.3, 0.25, 0, "SM_CardBoxC_01"), (0.25, 0.25, 0, "SM_CardBoxB_01"),
                                                  (-0.3, 0.25, 0.25, "SM_CardBoxD_01")]):
            self._add_prop(asset, f"/World/Outbound/carton_{i}", (outbound[0] + dx, outbound[1] + dy, 0.211 + dz), 0, (1, 1, 1))
        self.conveyor_end_y = end_y
        self.dispatch_totes: dict[str, str] = {}
        self.shipping: list[tuple[str, list[Box]]] = []
        self.tote_counter = 0
        for location in self.locations.values():
            if location["kind"] == "dispatch":
                self.dispatch_totes[location["id"]] = self._spawn_tote(float(location["y"]))

    def _spawn_tote(self, y: float) -> str:
        self.tote_counter += 1
        path = f"/World/Totes/tote_{self.tote_counter}"
        add_reference_to_stage(f"{self.assets_root}/Isaac/Props/KLT_Bin/small_KLT_visual.usd", path)
        self._place_prop(path, (TOTE_X, y, ROLLER_TOP + TOTE_SIZE[2] / 2), 0, TOTE_SCALE)
        return path

    def ship_tote(self, location_id: str) -> None:
        path = self.dispatch_totes[location_id]
        tote_y = float(self.locations[location_id]["y"])
        boxes = []
        for box in [b for b in self.boxes if b.location_id == location_id]:
            box.location_id = None
            boxes.append((box, box.position - np.array([TOTE_X, tote_y, 0.0])))
        self.shipping.append((path, boxes))
        self.dispatch_totes[location_id] = self._spawn_tote(float(self.locations[location_id]["y"]))
        self._hide(self.dispatch_totes[location_id])
        self.pending_tote = (location_id, 0.0)

    def update_conveyor(self) -> None:
        step = TOTE_SPEED * PHYSICS_DT
        arrived = []
        for path, boxes in self.shipping:
            translate = UsdGeom.Xformable(self.stage.GetPrimAtPath(path)).GetOrderedXformOps()[0]
            position = translate.Get()
            y = position[1] + step
            translate.Set(Gf.Vec3d(position[0], y, position[2]))
            for box, offset in boxes:
                box.move(np.array([TOTE_X, y, 0.0]) + offset, np.array([1.0, 0, 0, 0]))
            if y > self.conveyor_end_y - TOTE_SIZE[1] / 2:
                arrived.append((path, boxes))
        for path, boxes in arrived:
            self.shipping.remove((path, boxes))
            self.stage.RemovePrim(path)
            for box, _ in boxes:
                self.stage.RemovePrim(box.path)
                self.boxes.remove(box)
        if getattr(self, "pending_tote", None):
            location_id, waited = self.pending_tote
            if waited > 2.0:
                UsdGeom.Imageable(self.stage.GetPrimAtPath(self.dispatch_totes[location_id])).MakeVisible()
                self.pending_tote = None
            else:
                self.pending_tote = (location_id, waited + PHYSICS_DT)

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

    def _build_policy_cameras(self) -> None:
        camera = UsdGeom.Camera.Define(self.stage, "/World/HeadCam")
        camera.CreateFocalLengthAttr(10.0)
        camera.CreateHorizontalApertureAttr(20.955)
        camera.CreateVerticalApertureAttr(20.955)
        self.update_policy_cameras()
        for name, path in (("head", "/World/HeadCam"),):
            annotator = rep.AnnotatorRegistry.get_annotator("rgb")
            annotator.attach([rep.create.render_product(path, POLICY_RES)])
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

    def policy_observation(self) -> dict:
        opening = float(self.robot.get_joint_positions()[self.finger_idx].mean())
        state = np.concatenate([self.to_base_frame(self.fingertip()), [opening]]).astype(np.float32)
        return {
            "head": self.annotators["head"].get_data()[..., :3].copy(),
            "state": state,
            "finger_target": np.float32(self.finger_target),
        }

    def update_policy_cameras(self) -> None:
        x, y, theta = self.base_pose()
        forward = np.array([math.cos(theta), math.sin(theta), 0.0])
        base = np.array([x, y, 0.0])
        set_camera_view(eye=base + forward * 0.1 + np.array([0, 0, 1.25]),
                        target=base + forward * 0.95 + np.array([0, 0, 0.45]), camera_prim_path="/World/HeadCam")

    def fingertip(self) -> np.ndarray:
        position, orientation = self.hand.get_world_pose()
        return position + quat_to_rot_matrix(orientation) @ FINGERTIP_OFFSET

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
        inspect.CreateHorizontalApertureAttr(20.955)
        inspect.CreateVerticalApertureAttr(20.955 * INSPECT_RES[1] / INSPECT_RES[0])
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
        path = f"/World/Boxes/box_{self.box_counter}"
        if args.graphics == "high":
            prim = self._cardboard_box(path, position, rgb)
        else:
            prim = VisualCuboid(path, position=position, scale=BOX_SIZE, color=np.array(rgb))
        box = Box(prim, sku)
        box.location_id = location_id
        self.boxes.append(box)
        return box

    def _cardboard_box(self, path: str, position: np.ndarray, rgb) -> SingleXFormPrim:
        UsdGeom.Xform.Define(self.stage, path)
        self._add_prop("SM_CardBoxD_01", path + "/carton", (0, 0, -BOX_SIZE[2] / 2), 90,
                       [BOX_SIZE[1] / CARDBOX_SIZE[0], BOX_SIZE[0] / CARDBOX_SIZE[1], BOX_SIZE[2] / CARDBOX_SIZE[2]])
        VisualCuboid(path + "/label", translation=np.array([-BOX_SIZE[0] / 2 - 0.001, 0, 0]),
                     scale=np.array([0.002, BOX_SIZE[1] * 0.7, BOX_SIZE[2] * 0.6]), color=np.array(rgb))
        prim = SingleXFormPrim(path)
        prim.set_world_pose(position)
        return prim

    def slot_positions(self, location_id: str, count: int) -> list[np.ndarray]:
        location = self.locations[location_id]
        x, y, z = float(location["x"]), float(location["y"]), float(location["z"])
        if location["kind"] == "dispatch":
            z = ROLLER_TOP + 0.015 + BOX_SIZE[2] / 2
            return [np.array([TOTE_X + (i % 2 - 0.5) * 0.13, y + (i // 2 % 4 - 1.5) * 0.125,
                              z + (i // 8) * BOX_SIZE[2]]) for i in range(count)]
        if location["kind"] == "staging":
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
        for path, _ in self.shipping:
            self.stage.RemovePrim(path)
        self.shipping = []
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
        if location["kind"] == "dispatch":
            return np.array([x - DISPATCH_STANDOFF, y, 0.0])
        if location["kind"] == "staging":
            return np.array([x - 0.6, y, 0.0])
        return np.array([x, y, 0.0])

    def solve_ik(self, position: np.ndarray, orientation: np.ndarray = FRONT_GRASP) -> np.ndarray:
        base_position, base_orientation = self.link0.get_world_pose()
        self.ik.set_robot_base_pose(base_position, base_orientation)
        seed = self.arm_target.copy()
        solutions = []
        for attempt in range(IK_ATTEMPTS):
            warm_start = seed if attempt == 0 else seed + np.random.normal(0.0, 0.15, size=seed.shape)
            joints, success = self.ik.compute_inverse_kinematics(
                "right_gripper", position, orientation, warm_start=warm_start)
            if success:
                solutions.append(np.array(joints))
        if not solutions:
            raise SimError("ik_failed", f"no arm solution for target {np.round(position, 2).tolist()}")
        return min(solutions, key=lambda joints: float(np.linalg.norm(joints - seed)))


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

    def reach(self, position: np.ndarray, steps: int = 90, orientation: np.ndarray = FRONT_GRASP):
        joints = self.w.solve_ik(position, orientation)
        yield from self.move_arm(joints, steps)

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

    def target_box(self, sku: str, location_id: str) -> Box:
        candidates = [b for b in self.w.boxes_at(location_id) if b.sku == sku]
        if not candidates:
            raise SimError("item_not_found", f"no {sku} at {location_id}")
        base = self.w.base_pose()
        return min(candidates, key=lambda b: np.linalg.norm(b.prim.get_world_pose()[0][:2] - base[:2]))

    def grasp_scripted(self, box: Box):
        position, _ = box.prim.get_world_pose()
        yield from self.gripper(0.04)
        yield from self.reach(position + np.array([-0.16, 0, 0.02]))
        yield from self.reach(position + np.array([-0.02, 0, 0.0]), 50)
        yield from self.gripper(0.0)
        return True

    def grasp_vla(self, box: Box, instruction: str):
        position, _ = box.prim.get_world_pose()
        self.w.finger_target = 0.04
        self.last_vla_miss = None
        for step in range(VLA_MAX_STEPS):
            obs = self.w.policy_observation()
            body = json.dumps({
                "instruction": instruction, "state": obs["state"].tolist(),
                "head_jpeg_b64": encode_jpeg(obs["head"]),
            }).encode()
            request = urllib.request.Request(args.policy_url + "/act", data=body, headers={"content-type": "application/json"})
            with urllib.request.urlopen(request, timeout=30) as response:
                actions = np.array(json.loads(response.read())["actions"], dtype=float)
            for action in actions[:VLA_EXECUTE_ACTIONS]:
                start_arm, start_finger = self.w.arm_target.copy(), self.w.finger_target
                try:
                    goal_arm = self.w.solve_ik(self.w.to_world_frame(action[:3]))
                except SimError:
                    goal_arm = start_arm
                for i in range(1, CONTROL_EVERY + 1):
                    t = i / CONTROL_EVERY
                    self.w.arm_target = start_arm + (goal_arm - start_arm) * t
                    self.w.finger_target = float(np.clip(start_finger + (action[3] - start_finger) * t, 0.0, 0.04))
                    yield
                if self.w.finger_target < 0.015:
                    for _ in range(15):
                        yield
                    self.last_vla_miss = float(np.linalg.norm(self.w.fingertip() - position))
                    return self.last_vla_miss < GRASP_TOLERANCE
        return False

    def pick(self, sku: str, location_id: str, fail: bool = False, policy: str = "scripted"):
        box = self.target_box(sku, location_id)
        position, _ = box.prim.get_world_pose()
        used = "scripted"
        vla_success = None
        if policy in ("vla", "vla_with_fallback"):
            color = self.w.colors.get(sku, "")
            vla_success = yield from self.grasp_vla(box, f"pick up the {color} box")
            used = "vla"
            if not vla_success:
                yield from self.gripper(0.04)
                yield from self.move_arm(self.w.arm_home, 50)
                if policy == "vla":
                    raise SimError("grasp_failed", f"VLA grasp of {sku} at {location_id} missed")
                used = "scripted_fallback"
                yield from self.grasp_scripted(box)
        else:
            yield from self.grasp_scripted(box)
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
        return {"picked": sku, "tote": [b.sku for b in self.w.tote], "policy_used": used, "vla_success": vla_success}

    def place(self, location_id: str):
        location = self.w.locations[location_id]
        dispatch = location["kind"] == "dispatch"
        if dispatch:
            drop = np.array([TOTE_X, float(location["y"]), ROLLER_TOP + TOTE_SIZE[2] + 0.05])
            yield from self.reach_any(drop, 80, RELEASE_POSES)
        else:
            drop = np.array([float(location["x"]) - 0.05, float(location["y"]), 0.45])
            yield from self.reach(drop, 80)
        placed = []
        existing = len(self.w.boxes_at(location_id))
        for i, (box, position) in enumerate(zip(list(self.w.tote), self.w.slot_positions(location_id, existing + len(self.w.tote))[existing:])):
            self.w.tote.remove(box)
            box.move(position + np.array([0, 0, 0.05]), np.array([1.0, 0, 0, 0]))
            box.location_id = location_id
            placed.append(box.sku)
            for _ in range(6):
                yield
        self.w.tote = []
        yield from self.move_arm(self.w.arm_home, 60)
        if dispatch:
            self.w.ship_tote(location_id)
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
                    if name in ("reset", "state", "counts", "view", "camera", "debug"):
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
            self.w.update_conveyor()
            self.w.update_chase_camera()
            self.w.update_policy_cameras()
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
    positions[warehouse.arm_idx] = warehouse.arm_home
    positions[warehouse.finger_idx] = 0.04
    warehouse.robot.set_joint_positions(positions)
    warehouse.base_target = pose.copy()
    warehouse.arm_target = warehouse.arm_home.copy()
    warehouse.finger_target = 0.04


def step(warehouse: Warehouse) -> None:
    warehouse.apply_targets()
    warehouse.update_attachments()
    warehouse.update_policy_cameras()
    warehouse.world.step(render=True)


def collect_demos(warehouse: Warehouse, episodes: int) -> None:
    from pathlib import Path

    rng = np.random.default_rng(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    commands = Commands(warehouse)
    stocked = sorted({(b.location_id, b.sku) for b in warehouse.boxes if b.location_id
                      and warehouse.locations[b.location_id]["kind"] == "storage"})
    log = open(out / "collect_log.jsonl", "a")
    saved = 0
    attempt = 0
    while saved < episodes and attempt < episodes * 3:
        attempt += 1
        location_id, sku = stocked[rng.integers(len(stocked))]
        jitter = np.array([rng.uniform(-0.06, 0.06), rng.uniform(-0.25, 0.25), rng.uniform(-0.08, 0.08)])
        teleport_base(warehouse, warehouse.approach_pose(location_id) + jitter)
        for _ in range(20):
            step(warehouse)
        box = commands.target_box(sku, location_id)
        frames = {"head": [], "state": [], "finger_target": []}
        generator = commands.grasp_scripted(box)
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
        except SimError as err:
            log.write(json.dumps({"attempt": attempt, "skipped": str(err)}) + "\n")
            continue
        for _ in range(3 * CONTROL_EVERY):
            step(warehouse)
            if _ % CONTROL_EVERY == 0:
                for key, value in warehouse.policy_observation().items():
                    frames[key].append(value)
        success = bool(np.linalg.norm(warehouse.fingertip() - box.prim.get_world_pose()[0]) < GRASP_TOLERANCE)
        if not success:
            log.write(json.dumps({"attempt": attempt, "skipped": "scripted grasp missed"}) + "\n")
            continue
        instruction = f"pick up the {warehouse.colors.get(sku, '')} box"
        state = np.stack(frames["state"])
        next_tip = np.concatenate([state[1:, :3], state[-1:, :3]])
        action = np.concatenate([next_tip, np.array(frames["finger_target"])[:, None]], axis=1).astype(np.float32)
        np.savez_compressed(
            out / f"episode_{saved:04d}.npz", instruction=instruction,
            head=np.stack(frames["head"]), state=state, action=action,
        )
        log.write(json.dumps({"episode": saved, "location_id": location_id, "sku": sku,
                              "frames": len(frames["state"]), "instruction": instruction}) + "\n")
        log.flush()
        saved += 1


def run_trial(warehouse: Warehouse, commands: "Commands", pose: np.ndarray, box: Box, policy: str) -> dict:
    teleport_base(warehouse, pose)
    for _ in range(20):
        step(warehouse)
    generator = commands.grasp_vla(box, f"pick up the {warehouse.colors.get(box.sku, '')} box") if policy == "vla" \
        else commands.grasp_scripted(box)
    ticks, result = 0, False
    try:
        while True:
            next(generator)
            step(warehouse)
            ticks += 1
    except StopIteration as stop:
        result = stop.value
    except SimError:
        result = False
    if policy == "scripted":
        for _ in range(15):
            step(warehouse)
        result = bool(np.linalg.norm(warehouse.fingertip() - box.prim.get_world_pose()[0]) < GRASP_TOLERANCE)
    miss = getattr(commands, "last_vla_miss", None) if policy == "vla" else \
        float(np.linalg.norm(warehouse.fingertip() - box.prim.get_world_pose()[0]))
    return {"success": bool(result), "sim_seconds": ticks * PHYSICS_DT, "miss_m": miss}


def eval_grasps(warehouse: Warehouse, trials: int) -> None:
    from pathlib import Path

    rng = np.random.default_rng(args.seed)
    commands = Commands(warehouse)
    stocked = sorted({(b.location_id, b.sku) for b in warehouse.boxes if b.location_id
                      and warehouse.locations[b.location_id]["kind"] == "storage"})
    rows = []
    for trial in range(trials):
        location_id, sku = stocked[rng.integers(len(stocked))]
        jitter = np.array([rng.uniform(-0.06, 0.06), rng.uniform(-0.25, 0.25), rng.uniform(-0.08, 0.08)])
        pose = warehouse.approach_pose(location_id) + jitter
        box = commands.target_box(sku, location_id)
        row = {"trial": trial, "location_id": location_id, "sku": sku}
        for policy in ("scripted", "vla"):
            row[policy] = run_trial(warehouse, commands, pose, box, policy)
        rows.append(row)
        print(json.dumps(row), flush=True)
    summary = {
        "trials": trials,
        "scripted_success": sum(r["scripted"]["success"] for r in rows) / trials,
        "vla_success": sum(r["vla"]["success"] for r in rows) / trials,
        "vla_or_fallback_success": sum(r["vla"]["success"] or r["scripted"]["success"] for r in rows) / trials,
        "scripted_mean_s": float(np.mean([r["scripted"]["sim_seconds"] for r in rows])),
        "vla_mean_s": float(np.mean([r["vla"]["sim_seconds"] for r in rows])),
    }
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
            if name not in ("navigate", "inspect", "pick", "place", "stow", "reset", "view", "camera"):
                return self._json(404, {"error": "unknown_command"})
            self._json(*server.submit(name, payload))

    return Handler


def main() -> None:
    warehouse = Warehouse(streaming=not (args.collect or args.eval_grasp))
    warehouse.reset_inventory([])
    if args.collect:
        collect_demos(warehouse, args.collect)
        app.close()
        return
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
