import argparse
import base64
import io
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

import numpy as np
import torch
from PIL import Image
from lerobot.configs.policies import PreTrainedConfig
from lerobot.policies.factory import get_policy_class, make_pre_post_processors


class Policy:
    def __init__(self, checkpoint: str, device: str):
        self.device = device
        config = PreTrainedConfig.from_pretrained(checkpoint)
        self.kind = config.type
        self.policy = get_policy_class(config.type).from_pretrained(checkpoint).to(device).eval()
        self.image_key = next(iter(self.policy.config.image_features))
        self.preprocess, self.postprocess = make_pre_post_processors(self.policy.config, pretrained_path=checkpoint)
        self.lock = Lock()

    def act(self, instruction: str, state: list[float], head_jpeg_b64: str) -> np.ndarray:
        image = np.asarray(Image.open(io.BytesIO(base64.b64decode(head_jpeg_b64))).convert("RGB"))
        batch = {
            self.image_key: torch.from_numpy(image).permute(2, 0, 1).float().unsqueeze(0) / 255.0,
            "observation.state": torch.tensor([state], dtype=torch.float32),
            "task": [instruction],
        }
        with self.lock, torch.inference_mode():
            batch = self.preprocess(batch)
            if self.kind == "diffusion":
                batch["observation.state"] = batch["observation.state"].unsqueeze(1)
            chunk = self.policy.predict_action_chunk(batch)
            chunk = self.postprocess(chunk)
        return chunk.squeeze(0).float().cpu().numpy()


def make_handler(policy: Policy):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _json(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._json(200, {"status": "ok"})

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
            started = time.perf_counter()
            actions = policy.act(payload["instruction"], payload["state"], payload["head_jpeg_b64"])
            self._json(200, {"actions": actions.tolist(), "latency_ms": int((time.perf_counter() - started) * 1000)})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve a fine-tuned grasp policy (SmolVLA or diffusion) over HTTP")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--port", type=int, default=8030)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    policy = Policy(args.checkpoint, args.device)
    print(f"policy server ready on :{args.port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(policy)).serve_forever()


if __name__ == "__main__":
    main()
