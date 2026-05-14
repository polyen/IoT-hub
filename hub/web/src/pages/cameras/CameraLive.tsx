import { useEffect, useRef, useState } from "react";
import { DetectionOverlay } from "../../features/cv/DetectionOverlay";
import { useCameraStream } from "../../features/cv/useCameraStream";
import type { Camera } from "../../lib/types";

interface Props {
  camera: Camera;
  overlayEnabled: boolean;
  blurred?: boolean;
}

export function CameraLive({ camera, overlayEnabled, blurred = false }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [videoDims, setVideoDims] = useState({ w: 640, h: 360 });
  const frame = useCameraStream(camera.id);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !camera.stream_hls) return;

    let hls: import("hls.js").default | null = null;

    import("hls.js").then(({ default: Hls }) => {
      if (!videoRef.current) return;
      if (Hls.isSupported()) {
        hls = new Hls({ lowLatencyMode: true });
        hls.loadSource(camera.stream_hls!);
        hls.attachMedia(videoRef.current);
      } else if (videoRef.current.canPlayType("application/vnd.apple.mpegurl")) {
        videoRef.current.src = camera.stream_hls!;
      }
    });

    return () => { hls?.destroy(); };
  }, [camera.stream_hls]);

  const handleLoadedMetadata = () => {
    const v = videoRef.current;
    if (v) setVideoDims({ w: v.videoWidth || 640, h: v.videoHeight || 360 });
  };

  return (
    <div className={`relative bg-black rounded-xl overflow-hidden${blurred ? " select-none" : ""}`}>
      {camera.stream_hls ? (
        <>
          <video
            ref={videoRef}
            className={`w-full transition-all duration-300${blurred ? " blur-xl brightness-50" : ""}`}
            autoPlay
            muted
            playsInline
            onLoadedMetadata={handleLoadedMetadata}
          />
          {blurred && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <span className="text-white text-sm bg-black/60 px-3 py-1.5 rounded-full">🔒 Приватний режим</span>
            </div>
          )}
          <DetectionOverlay
            frame={frame}
            videoWidth={videoDims.w}
            videoHeight={videoDims.h}
            visible={overlayEnabled && !blurred}
          />
        </>
      ) : (
        <div className="aspect-video flex items-center justify-center text-slate-600">
          <span className="text-sm">Немає потоку</span>
        </div>
      )}

      {/* camera name overlay */}
      <div className="absolute top-2 left-2 text-xs bg-black/60 text-white px-2 py-1 rounded">
        {camera.name}
        {!camera.online && <span className="ml-1 text-red-400">● offline</span>}
      </div>
    </div>
  );
}
