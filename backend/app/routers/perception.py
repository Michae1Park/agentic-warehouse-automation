import base64

from fastapi import APIRouter
from pydantic import BaseModel

from .. import perception

router = APIRouter(prefix="/perception", tags=["perception"])


class IdentifyRequest(BaseModel):
    location_id: str
    image_jpeg_b64: str


@router.post("/identify")
async def identify(request: IdentifyRequest):
    return await perception.identify_products(base64.b64decode(request.image_jpeg_b64), request.location_id)
