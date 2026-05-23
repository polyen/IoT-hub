import { useEffect, useRef, useState } from "react";
import { DetectionOverlay } from "../../features/cv/DetectionOverlay";
import { useCameraStream } from "../../features/cv/useCameraStream";
import type { Camera } from "../../lib/types";

interface Props {
  camera: Camera;
  overlayEnabled: boolean;
  blurred?: boolean;
}

// Derive WHEP URL from HLS URL: /hls/camera/index.m3u8 → /whep/camera/whep
function whepUrl(hlsUrl: string | null | undefined): string | null {
  if (!hlsUrl) return null;
  const m = hlsUrl.match(/^\/hls\/(.+)\/index\.m3u8$/);
  return m ? `/whep/${m[1]}/whep` : null;
}

async function connectWhep(url: string, video: HTMLVideoElement): Promise<RTCPeerConnection> {
  const pc = new RTCPeerConnection({ iceServers: [] });
  const stream = new MediaStream();
  video.srcObject = stream;
  pc.ontrack = (e) => stream.addTrack(e.track);
  pc.addTransceiver("video", { direction: "recvonly" });

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  // Wait for ICE gathering (max 3s then proceed with partial candidates)
  await new Promise<void>((resolve) => {
    if (pc.iceGatheringState === "complete") { resolve(); return; }
    const timer = setTimeout(resolve, 3000);
    pc.addEventListener("icegatheringstatechange", () => {
      if (pc.iceGatheringState === "complete") { clearTimeout(timer); resolve(); }
    });
  });

  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/sdp" },
    body: pc.localDescription!.sdp,
  });
  if (!resp.ok) throw new Error(`WHEP ${resp.status}`);
  const sdp = await resp.text();
  await pc.setRemoteDescription({ type: "answer", sdp });
  return pc;
}

export function CameraLive({ camera, overlayEnabled, blurred = false }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [videoDims, setVideoDims] = useState({ w: 640, h: 360 });
  const frame = useCameraStream(camera.id);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    let pc: RTCPeerConnection | null = null;
    let hls: import("hls.js").default | null = null;
    let cancelled = false;

    const whep = whepUrl(camera.stream_hls);

    if (whep) {
      connectWhep(whep, video)
        .then((conn) => { if (!cancelled) pc = conn; })
        .catch(() => {
          // WebRTC failed — fall back to HLS
          if (cancelled || !camera.stream_hls) return;
          import("hls.js").then(({ default: Hls }) => {
            if (cancelled || !videoRef.current) return;
            if (Hls.isSupported()) {
              hls = new Hls({
                lowLatencyMode: true,
                liveSyncDurationCount: 1,
                liveMaxLatencyDurationCount: 2,
                maxBufferLength: 2,
                maxMaxBufferLength: 4,
              });
              hls.loadSource(camera.stream_hls!);
              hls.attachMedia(videoRef.current);
            } else if (videoRef.current.canPlayType("application/vnd.apple.mpegurl")) {
              videoRef.current.src = camera.stream_hls!;
            }
          });
        });
    } else if (camera.stream_hls) {
      import("hls.js").then(({ default: Hls }) => {
        if (cancelled || !videoRef.current) return;
        if (Hls.isSupported()) {
          hls = new Hls({ lowLatencyMode: true });
          hls.loadSource(camera.stream_hls!);
          hls.attachMedia(videoRef.current);
        } else if (videoRef.current.canPlayType("application/vnd.apple.mpegurl")) {
          videoRef.current.src = camera.stream_hls!;
        }
      });
    }

    return () => {
      cancelled = true;
      pc?.close();
      hls?.destroy();
      if (video.srcObject instanceof MediaStream) {
        (video.srcObject as MediaStream).getTracks().forEach((t) => t.stop());
        video.srcObject = null;
      }
    };
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
