from pydantic import BaseModel


class CameraOut(BaseModel):
    id: str
    name: str
    stream_hls: str | None
    stream_webrtc: str | None
    online: bool
