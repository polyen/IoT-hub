export default function AboutPage() {
  return (
    <div>
      <h1 className="text-xl font-semibold mb-4">IoT Hub</h1>
      <div className="bg-slate-800 light:bg-white rounded-xl p-4 border border-slate-700 light:border-slate-200 space-y-2 text-sm text-slate-300 light:text-slate-700">
        <p>Local-first privacy-preserving IoT Hub</p>
        <p>Edge: Raspberry Pi 5 + Hailo-8 NPU</p>
        <p className="text-slate-500">v0.1.0</p>
      </div>
    </div>
  );
}
