import argparse
import shutil
from pathlib import Path

import numpy as np
from lerobot.datasets.lerobot_dataset import LeRobotDataset

JOINTS = [f"panda_joint{i}" for i in range(1, 8)] + ["gripper"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert raw Isaac grasp demos into a LeRobot dataset")
    parser.add_argument("--raw", default="vla/data/raw")
    parser.add_argument("--root", default="vla/data/lerobot")
    parser.add_argument("--repo-id", default="local/warehouse_shelf_grasp")
    parser.add_argument("--fps", type=int, default=10)
    args = parser.parse_args()

    root = Path(args.root)
    if root.exists():
        shutil.rmtree(root)
    episodes = sorted(Path(args.raw).glob("episode_*.npz"))
    sample = np.load(episodes[0])
    height, width, _ = sample["head"].shape[1:]

    dataset = LeRobotDataset.create(
        repo_id=args.repo_id, fps=args.fps, root=root, robot_type="ridgeback_franka", use_videos=False,
        features={
            "observation.images.head": {"dtype": "image", "shape": (height, width, 3),
                                        "names": ["height", "width", "channel"]},
            "observation.state": {"dtype": "float32", "shape": (len(JOINTS),), "names": JOINTS},
            "action": {"dtype": "float32", "shape": (len(JOINTS),), "names": JOINTS},
        },
    )
    for path in episodes:
        episode = np.load(path)
        task = str(episode["instruction"])
        for i in range(len(episode["state"])):
            dataset.add_frame({
                "observation.images.head": episode["head"][i],
                "observation.state": episode["state"][i].astype(np.float32),
                "action": episode["action"][i].astype(np.float32),
                "task": task,
            })
        dataset.save_episode()
    dataset.finalize()
    print(f"wrote {len(episodes)} episodes, {dataset.num_frames} frames to {root}")


if __name__ == "__main__":
    main()
