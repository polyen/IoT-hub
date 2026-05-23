from typing import Literal

from pydantic import BaseModel


class AudioDevice(BaseModel):
    id: str
    name: str
    type: Literal["rtsp_mic", "local_mic", "rtsp_speaker", "local_speaker"]
    available: bool


class AudioConfig(BaseModel):
    input_id: str | None = None
    output_id: str | None = None
