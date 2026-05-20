const { chromium } = require('@playwright/test');

(async () => {
  const executablePath = process.argv[2];
  const browser = await chromium.launch({ headless: true, executablePath });

  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'dark' });
  const page = await ctx.newPage();
  await page.addInitScript(() => localStorage.setItem('theme', 'dark'));
  await page.goto('http://localhost:3456/more/events', { waitUntil: 'networkidle', timeout: 15000 });

  // Inject mock events into the WebSocket IDB cache, then reload
  await page.evaluate(() => {
    const now = new Date();
    const ts = (offsetMin) => new Date(now - offsetMin * 60000).toISOString();

    const mockEvents = [
      { id: "1", type: "fire", room: "Кухня", tier: 0, timestamp: ts(1), payload: { class: "fire", conf: 0.92 }, model_version: "yolo26n-v3" },
      { id: "2", type: "camera/identity", room: "Вітальня", tier: 1, timestamp: ts(3), payload: { name: "Влад", confidence: 0.97, track_id: 4 }, model_version: null },
      { id: "3", type: "stranger", room: "Коридор", tier: 0, timestamp: ts(7), payload: { conf: 0.78, track_id: 9 }, model_version: null },
      { id: "4", type: "fall_detected", room: "Спальня", tier: 0, timestamp: ts(12), payload: { confidence: 0.89 }, model_version: "yolov8s_pose" },
      { id: "5", type: "sensor/dht", room: "Кухня", tier: 2, timestamp: ts(15), payload: { temperature: 23.4, humidity: 61.0 }, model_version: null },
      { id: "6", type: "motion", room: "Коридор", tier: 2, timestamp: ts(22), payload: { pir: true }, model_version: null },
      { id: "7", type: "sensor/mq2", room: "Кухня", tier: 1, timestamp: ts(35), payload: { ppm: 87, raw: 312 }, model_version: null },
      { id: "8", type: "camera/event", room: "Вітальня", tier: 1, timestamp: ts(48), payload: { class: "person", conf: 0.95, track_id: 2 }, model_version: "yolo26n-v3" },
      { id: "9", type: "sensor/door", room: "Коридор", tier: 2, timestamp: ts(60), payload: { open: true }, model_version: null },
    ];

    // Write to IDB
    const req = indexedDB.open("iot-hub", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("events", { keyPath: "id" });
    req.onsuccess = () => {
      const db = req.result;
      const tx = db.transaction("events", "readwrite");
      const store = tx.objectStore("events");
      mockEvents.forEach(e => store.put(e));
    };
  });

  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(1000);

  await page.screenshot({ path: '/tmp/events_dark_mobile.png' });
  await ctx.close();

  // Light mode
  const ctx2 = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'light' });
  const page2 = await ctx2.newPage();
  await page2.addInitScript(() => localStorage.setItem('theme', 'light'));
  await page2.goto('http://localhost:3456/more/events', { waitUntil: 'networkidle', timeout: 15000 });
  await page2.evaluate(() => {
    const now = new Date();
    const ts = (offsetMin) => new Date(now - offsetMin * 60000).toISOString();
    const mockEvents = [
      { id: "1", type: "fire", room: "Кухня", tier: 0, timestamp: ts(1), payload: { class: "fire", conf: 0.92 }, model_version: "yolo26n-v3" },
      { id: "2", type: "camera/identity", room: "Вітальня", tier: 1, timestamp: ts(3), payload: { name: "Влад", confidence: 0.97, track_id: 4 }, model_version: null },
      { id: "3", type: "stranger", room: "Коридор", tier: 0, timestamp: ts(7), payload: { conf: 0.78, track_id: 9 }, model_version: null },
      { id: "4", type: "fall_detected", room: "Спальня", tier: 0, timestamp: ts(12), payload: { confidence: 0.89 }, model_version: "yolov8s_pose" },
      { id: "5", type: "sensor/dht", room: "Кухня", tier: 2, timestamp: ts(15), payload: { temperature: 23.4, humidity: 61.0 }, model_version: null },
      { id: "6", type: "motion", room: "Коридор", tier: 2, timestamp: ts(22), payload: { pir: true }, model_version: null },
    ];
    const req = indexedDB.open("iot-hub", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("events", { keyPath: "id" });
    req.onsuccess = () => {
      const db = req.result;
      const tx = db.transaction("events", "readwrite");
      const store = tx.objectStore("events");
      mockEvents.forEach(e => store.put(e));
    };
  });
  await page2.reload({ waitUntil: 'networkidle' });
  await page2.waitForTimeout(1000);
  await page2.screenshot({ path: '/tmp/events_light_mobile.png' });
  await ctx2.close();

  await browser.close();
  console.log('done');
})();
