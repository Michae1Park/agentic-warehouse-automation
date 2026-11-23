import base64

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import perception

router = APIRouter(prefix="/perception", tags=["perception"])


class IdentifyRequest(BaseModel):
    location_id: str
    image_jpeg_b64: str


@router.post("/identify")
async def identify(request: IdentifyRequest):
    return await perception.identify_products(base64.b64decode(request.image_jpeg_b64), request.location_id)


class GroundRequest(BaseModel):
    location_id: str
    query: str


@router.post("/ground")
async def ground(request: GroundRequest, http_request: Request):
    robot = next(iter(http_request.app.state.robots.values()))
    if not hasattr(robot, "resolve"):
        raise HTTPException(400, "grounding needs the Isaac robot backend")
    observation = await robot.inspect(request.location_id)
    result = await perception.ground(observation.image_jpeg, request.query)
    hit = await robot.resolve(request.location_id, result["point"]) if result["point"] else None
    return {**result, "hit": hit}
