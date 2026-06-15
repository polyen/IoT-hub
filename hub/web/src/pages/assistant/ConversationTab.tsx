import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { relativeTime } from "../../lib/format";

interface VoiceMessage {
  type: "transcript" | "wakeword" | "agent_result";
  text: string;
  ts: string;
  confidence?: number;
  action_class?: string;
  tool?: string;
}

// ── Push-to-Talk ────────────────────────────────────────────────────────────

function PushToTalk() {
  const [recording, setRecording] = useState(false);
  const [uploading, setUploading] = useState(false);
  const mrRef = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);

  async function startRecording() {
    if (recording) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "audio/ogg";
      const mr = new MediaRecorder(stream, { mimeType });
      chunks.current = [];
      mr.ondataavailable = (e) => { if (e.data.size > 0) chunks.current.push(e.data); };
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks.current, { type: mimeType });
        setUploading(true);
        try {
          await fetch("/api/agent/voice/audio", {
            method: "POST",
            body: blob,
            headers: { "Content-Type": mimeType },
          });
          toast.success("Аудіо відправлено у пайплайн");
        } catch {
          toast.error("Не вдалося відправити аудіо");
        } finally {
          setUploading(false);
        }
      };
      mr.start(100); // collect in 100ms chunks
      mrRef.current = mr;
      setRecording(true);
      if ("vibrate" in navigator) navigator.vibrate(50);
    } catch {
      toast.error("Немає доступу до мікрофона");
    }
  }

  function stopRecording() {
    if (!recording || !mrRef.current) return;
    mrRef.current.stop();
    mrRef.current = null;
    setRecording(false);
    if ("vibrate" in navigator) navigator.vibrate([30, 30, 30]);
  }

  return (
    <div className="flex flex-col items-center gap-3 py-6">
      <button
        onPointerDown={startRecording}
        onPointerUp={stopRecording}
        onPointerLeave={stopRecording}
        disabled={uploading}
        className={[
          "w-20 h-20 rounded-full text-2xl transition-all duration-150 select-none touch-none",
          "border-4 shadow-lg active:scale-95",
          recording
            ? "bg-red-600 border-red-400 animate-pulse"
            : "bg-slate-700 border-slate-500 hover:bg-slate-600",
          uploading ? "opacity-50 cursor-wait" : "cursor-pointer",
        ].join(" ")}
        aria-label={recording ? "Зупинити запис" : "Утримуй для запису"}
      >
        {uploading ? "⏳" : recording ? "🔴" : "🎤"}
      </button>
      <p className="text-xs text-slate-500">
        {uploading ? "Відправляємо…" : recording ? "Запис… відпусти щоб відправити" : "Утримуй для запису"}
      </p>
    </div>
  );
}

// ── Conversation (live transcripts + agent results) ─────────────────────────

export default function ConversationTab() {
  const [messages, setMessages] = useState<VoiceMessage[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const voiceWs = new WebSocket(`${proto}://${location.host}/api/agent/ws/voice`);
    const agentWs = new WebSocket(`${proto}://${location.host}/api/agent/ws/agent`);
    wsRef.current = voiceWs;

    voiceWs.onopen = () => setConnected(true);
    voiceWs.onclose = () => setConnected(false);
    voiceWs.onerror = () => voiceWs.close();
    voiceWs.onmessage = (e) => {
      try {
        const msg: VoiceMessage = JSON.parse(e.data as string);
        setMessages((prev) => [msg, ...prev].slice(0, 100));
      } catch { /* ignore */ }
    };

    agentWs.onerror = () => agentWs.close();
    agentWs.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data as string);
        if (ev.type !== "result" || !ev.text || ev.action_class === "DENY" || ev.action_class === "ERROR") return;
        const msg: VoiceMessage = {
          type: "agent_result",
          text: ev.text as string,
          ts: ev.ts ?? new Date().toISOString(),
          action_class: ev.action_class as string,
          tool: ev.tool as string | undefined,
        };
        setMessages((prev) => [msg, ...prev].slice(0, 100));
      } catch { /* ignore */ }
    };

    return () => { voiceWs.close(); agentWs.close(); };
  }, []);

  return (
    <div className="space-y-4">
      <PushToTalk />

      <div className="flex items-center gap-2 text-sm">
        <span className={`h-2 w-2 rounded-full ${connected ? "bg-green-500" : "bg-slate-500"}`} />
        <span className="text-slate-400">{connected ? "Підключено" : "Очікування підключення…"}</span>
      </div>

      {messages.length === 0 ? (
        <div className="py-8 text-center text-slate-500">
          <p className="text-sm">Очікування голосових подій…</p>
          <p className="text-xs mt-1 text-slate-600">Скажіть ключове слово або скористайтесь кнопкою вище</p>
        </div>
      ) : (
        <div className="space-y-2">
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`rounded-lg px-4 py-3 text-sm border ${msg.type === "wakeword"
                  ? "border-blue-700 bg-blue-900/30 text-blue-200"
                  : msg.type === "agent_result"
                    ? "border-green-700/60 bg-green-950/30 text-green-200"
                    : "border-slate-700 bg-slate-800/60"
                }`}
            >
              <div className="flex items-start justify-between gap-2">
                <p className="flex-1">{msg.text}</p>
                <span className="shrink-0 text-xs text-slate-500">{relativeTime(msg.ts)}</span>
              </div>
              <p className="mt-1 text-xs text-slate-500">
                {msg.type === "wakeword" && "Ключове слово"}
                {msg.type === "transcript" && (msg.confidence !== undefined ? `Транскрипція · ${Math.round(msg.confidence * 100)}%` : "Транскрипція")}
                {msg.type === "agent_result" && (
                    msg.action_class === "AUTO"   ? "✅ Виконано" :
                    msg.action_class === "WARN"   ? "⚠️ Попередження" :
                    msg.action_class === "DENY"   ? "🚫 Заблоковано" :
                    msg.action_class === "INFO"   ? "ℹ️ Відповідь" :
                    "Відповідь"
                  )}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
