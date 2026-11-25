import argparse
import shutil
from pathlib import Path

import numpy as np
from lerobot.datasets.lerobot_dataset import LeRobotDataset

ORIENTATION_NAMES = {"euler": ["yaw", "pitch", "roll"], "rot6d": ["r11", "r21", "r31", "r12", "r22", "r32"]}
CAMERAS = ("head", "wrist")
MARKER_COLOR = np.array([255, 0, 255], dtype=np.uint8)
MIN_SEGMENT = 5


def marker_center(image: np.ndarray) -> np.ndarray:
    rows, columns = np.nonzero(np.all(image == MARKER_COLOR, axis=-1))
    if not len(rows):
        return np.array([-1.0, -1.0], dtype=np.float32)
    return np.array([columns.mean() / image.shape[1], rows.mean() / image.shape[0]], dtype=np.float32)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert raw Isaac grasp demos into a LeRobot dataset")
    parser.add_argument("--raw", nargs="+", default=["vla/data/raw_scenes"])
    parser.add_argument("--root", default="vla/data/lerobot_scenes")
    parser.add_argument("--repo-id", default="local/warehouse_scenes_grasp")
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--orientation", choices=sorted(ORIENTATION_NAMES), default="euler")
    parser.add_argument("--marker-state", action="store_true", help="append the head-image marker center to the state")
    parser.add_argument("--human-only", action="store_true", help="keep only teleoperated segments of episodes that carry a human mask")
    parser.add_argument("--human-repeat", type=int, default=1, help="repeat episodes that carry a human mask this many times")
    args = parser.parse_args()

    joints = ["tip_x", "tip_y", "tip_z", *ORIENTATION_NAMES[args.orientation], "gripper"]
    state_names = joints + (["marker_u", "marker_v"] if args.marker_state else [])
    root = Path(args.root)
    if root.exists():
        shutil.rmtree(root)
    episodes = [path for raw in args.raw for path in sorted(Path(raw).glob("episode_*.npz"))]
    sample = np.load(episodes[0])
    height, width, _ = sample["head"].shape[1:]

    dataset = LeRobotDataset.create(
        repo_id=args.repo_id, fps=args.fps, root=root, robot_type="ridgeback_franka", use_videos=False,
        image_writer_processes=8, image_writer_threads=2,
        features={
            **{f"observation.images.{camera}": {"dtype": "image", "shape": (height, width, 3),
                                                "names": ["height", "width", "channel"]} for camera in CAMERAS},
            "observation.state": {"dtype": "float32", "shape": (len(state_names),), "names": state_names},
            "action": {"dtype": "float32", "shape": (len(joints),), "names": joints},
        },
    )
    for path in episodes:
        episode = dict(np.load(path))
        task = str(episode["instruction"])
        segments = [np.arange(len(episode["state"]))]
        repeat = 1
        if "human" in episode:
            repeat = args.human_repeat
            if args.human_only:
                segments = [run for run in np.split(np.arange(len(episode["human"])), np.flatnonzero(np.diff(episode["human"].astype(int))) + 1)
                            if episode["human"][run[0]] and len(run) >= MIN_SEGMENT]
        for _ in range(repeat):
            for frames in segments:
                for i in frames:
                    state = episode["state"][i]
                    if args.marker_state:
                        state = np.concatenate([state, marker_center(episode["head"][i])])
                    dataset.add_frame({
                        **{f"observation.images.{camera}": episode[camera][i] for camera in CAMERAS},
                        "observation.state": state.astype(np.float32),
                        "action": episode["action"][i].astype(np.float32),
                        "task": task,
                    })
                dataset.save_episode()
    dataset.finalize()
    print(f"wrote {len(episodes)} episodes, {dataset.num_frames} frames to {root}")


if __name__ == "__main__":
    main()
