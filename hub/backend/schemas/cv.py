from pydantic import BaseModel, Field


class CameraOut(BaseModel):
    id: str
    name: str
    stream_hls: str | None
    stream_webrtc: str | None
    online: bool


class BBoxIn(BaseModel):
    class_id: int = Field(ge=0, le=2)  # 0=person 1=fire 2=smoke
    cx: float = Field(ge=0.0, le=1.0)
    cy: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)


class AnnotationRequest(BaseModel):
    image_b64: str
    boxes: list[BBoxIn]
