// Custom service worker additions for IoT Hub PWA
// NOTE: This file is NOT yet auto-injected into the Workbox SW.
// It is scaffolding for future use with `strategies: "injectManifest"`.
// Currently vite.config.ts uses registerType: "autoUpdate" (Workbox generateSW).
// To activate: switch VitePWA to injectManifest strategy, set srcDir: "src",
// filename: "sw.ts", and create src/sw.ts that imports this file alongside
// the Workbox precache manifest.

// Background sync for feedback submissions that failed while offline
self.addEventListener("sync", (event: Event) => {
  const syncEvent = event as SyncEvent;
  if (syncEvent.tag === "feedback-sync") {
    syncEvent.waitUntil(syncPendingFeedback());
  }
});

async function syncPendingFeedback(): Promise<void> {
  const db = await openFeedbackDB();
  const pending = await getPendingFeedback(db);
  for (const item of pending) {
    try {
      const resp = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(item.payload),
      });
      if (resp.ok) {
        await deleteFeedback(db, item.id);
      }
    } catch {
      // Will retry on next sync event
    }
  }
}

async function openFeedbackDB(): Promise<IDBDatabase> {
  return new Promise<IDBDatabase>((resolve, reject) => {
    const req = indexedDB.open("iot-feedback", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("pending", { autoIncrement: true });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function getPendingFeedback(
  db: IDBDatabase,
): Promise<Array<{ id: number; payload: unknown }>> {
  return new Promise((resolve) => {
    const tx = db.transaction("pending", "readonly");
    const store = tx.objectStore("pending");
    const results: Array<{ id: number; payload: unknown }> = [];
    const req = store.openCursor();
    req.onsuccess = () => {
      const cursor = req.result;
      if (cursor) {
        results.push({ id: cursor.key as number, payload: cursor.value });
        cursor.continue();
      } else {
        resolve(results);
      }
    };
    req.onerror = () => resolve([]);
  });
}

async function deleteFeedback(db: IDBDatabase, id: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction("pending", "readwrite");
    const req = tx.objectStore("pending").delete(id);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

// --- Type declarations ---

interface SyncEvent extends Event {
  tag: string;
  waitUntil(promise: Promise<unknown>): void;
}
