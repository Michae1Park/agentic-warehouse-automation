import argparse
import json
import shutil
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from convert_dataset import marker_center

QUANTILES = {"q01": 0.01, "q10": 0.10, "q50": 0.50, "q90": 0.90, "q99": 0.99}


def stats(values: np.ndarray) -> dict:
    return {"min": values.min(0), "max": values.max(0), "mean": values.mean(0), "std": values.std(0),
            "count": np.array([len(values)]), **{k: np.quantile(values, q, axis=0) for k, q in QUANTILES.items()}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Append the head-image marker center to observation.state of a converted dataset")
    parser.add_argument("--raw", default="vla/data/push_v1")
    parser.add_argument("--src", default="vla/data/lerobot_push")
    parser.add_argument("--dst", default="vla/data/lerobot_push_marker")
    args = parser.parse_args()
    src, dst = Path(args.src), Path(args.dst)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src / "meta", dst / "meta")
    raw = sorted(Path(args.raw).glob("episode_*.npz"))
    markers = {i: np.stack([marker_center(frame) for frame in np.load(path)["head"]]) for i, path in enumerate(raw)}
    states = []
    for path in sorted((src / "data").rglob("*.parquet")):
        table = pq.read_table(path)
        state = np.array(table.column("observation.state").to_pylist(), dtype=np.float32)
        marker = np.stack([markers[e][f] for e, f in zip(table.column("episode_index").to_pylist(), table.column("frame_index").to_pylist())])
        state = np.concatenate([state, marker], axis=1)
        states.append(state)
        column = pa.FixedSizeListArray.from_arrays(pa.array(state.reshape(-1), pa.float32()), state.shape[1])
        table = table.set_column(table.column_names.index("observation.state"), "observation.state", column)
        metadata = json.loads(table.schema.metadata[b"huggingface"])
        metadata["info"]["features"]["observation.state"]["length"] = state.shape[1]
        table = table.replace_schema_metadata({**table.schema.metadata, b"huggingface": json.dumps(metadata).encode()})
        out = dst / path.relative_to(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, out)
    info = json.loads((dst / "meta/info.json").read_text())
    feature = info["features"]["observation.state"]
    feature["shape"] = [feature["shape"][0] + 2]
    feature["names"] = feature["names"] + ["marker_u", "marker_v"]
    (dst / "meta/info.json").write_text(json.dumps(info, indent=4))
    overall = json.loads((dst / "meta/stats.json").read_text())
    overall["observation.state"] = {k: v.tolist() for k, v in stats(np.concatenate(states)).items()}
    (dst / "meta/stats.json").write_text(json.dumps(overall, indent=4))
    for path in sorted((dst / "meta/episodes").rglob("*.parquet")):
        table = pq.read_table(path)
        for key in QUANTILES.keys() | {"min", "max", "mean", "std"}:
            name = f"stats/observation.state/{key}"
            values = [np.concatenate([np.array(old), stats(markers[e])[key]]).tolist()
                      for old, e in zip(table.column(name).to_pylist(), table.column("episode_index").to_pylist())]
            table = table.set_column(table.column_names.index(name), name, pa.array(values, table.column(name).type))
        pq.write_table(table, path)
    print(f"wrote {dst} with {sum(len(s) for s in states)} frames, state dim {states[0].shape[1]}")


if __name__ == "__main__":
    main()
