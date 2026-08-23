const demoState = {
  settings: {
    blenderPath: "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe",
    imageOutputPath: "D:\\Renders\\Images",
    animationOutputPath: "D:\\Renders\\Animations",
    imageCustomArguments: "--threads 8",
    animationCustomArguments: "--threads 8 --cycles-device CUDA"
  },
  notifications: { enabled: true, server: "https://ntfy.example.com", topic: "blender" },
  queue: [
    { id: "demo-1", kind: "render", filePath: "D:\\Projects\\ProductHero.blend", fileName: "ProductHero.blend", type: "image", outputFormat: "PNG", frameStart: 0, frameEnd: 0, outputName: "ProductHero_####", state: "Finished" },
    { id: "demo-2", kind: "render", filePath: "D:\\Projects\\CameraMove.blend", fileName: "CameraMove.blend", type: "animation", outputFormat: "PNG_SEQUENCE", frameStart: 1, frameEnd: 120, outputName: "CameraMove_####", state: "Waiting" },
    { id: "demo-3", kind: "render", filePath: "D:\\Projects\\Turntable.blend", fileName: "Turntable.blend", type: "animation", outputFormat: "MP4", frameStart: 1, frameEnd: 180, outputName: "Turntable", state: "Waiting" },
    { id: "demo-4", kind: "system", action: "shutdown", state: null }
  ],
  runtime: { status: "Idle", currentTaskId: null, progress: 0, eta: null, log: [] }
};

let demoTimer = null;

function demoJson(value, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" }
  }));
}

function startDemoRender() {
  clearInterval(demoTimer);
  const task = demoState.queue.find(item => item.kind === "render" && item.state !== "Finished") || demoState.queue.find(item => item.kind === "render");
  demoState.runtime = { status: "Active", currentTaskId: task?.id || null, progress: 0, eta: 96, log: ["Demo queue started.", `Rendering ${task?.fileName || "Scene.blend"}`] };
  if (task) task.state = "Rendering";
  demoTimer = setInterval(() => {
    demoState.runtime.progress = Math.min(100, Math.round(demoState.runtime.progress + 5));
    demoState.runtime.eta = Math.max(0, Math.round(96 * (1 - demoState.runtime.progress / 100)));
    demoState.runtime.log.push(`Fra: 42 | Remaining: 00:${String(demoState.runtime.eta).padStart(2, "0")}.00 | Rendered ${Math.max(1, Math.round(demoState.runtime.progress / 100 * 48))}/48 Tiles, Sample 384/512`);
    if (demoState.runtime.progress >= 100) {
      clearInterval(demoTimer);
      if (task) task.state = "Finished";
      demoState.runtime.status = "Finished";
      demoState.runtime.currentTaskId = null;
      demoState.runtime.log.push("Demo render finished successfully.", `Notification sent for ${task?.fileName || "Scene.blend"}.`);
    }
  }, 450);
}

window.fetch = async function demoFetch(resource, options = {}) {
  const url = String(resource);
  const method = (options.method || "GET").toUpperCase();
  if (url.endsWith("/api/data")) return demoJson(demoState);
  if (url.endsWith("/api/system-defaults")) return demoJson({ blenderFound: true, settings: { blenderPath: "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe", imageOutputPath: "D:\\BlenderCMD Render GUI\\output", animationOutputPath: "D:\\BlenderCMD Render GUI\\animations-output" } });
  if (url.includes("/api/dialog/file")) return demoJson({ path: "D:\\Projects\\NewScene.blend" });
  if (url.includes("/api/dialog/folder")) return demoJson({ path: "D:\\Renders\\Demo" });
  if (url.includes("/api/dialog/exe")) return demoJson({ path: "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe" });
  if (url.endsWith("/api/preflight")) return demoJson({ animations: [] });
  if (url.endsWith("/api/queue") && method === "POST") {
    demoState.queue = JSON.parse(options.body || "{}").queue || [];
    return demoJson({ ok: true });
  }
  if (url.endsWith("/api/settings") && method === "POST") {
    demoState.settings = JSON.parse(options.body || "{}");
    return demoJson({ ok: true });
  }
  if (url.endsWith("/api/run") && method === "POST") {
    startDemoRender();
    return demoJson({ ok: true });
  }
  if (url.endsWith("/api/cancel") && method === "POST") {
    clearInterval(demoTimer);
    demoState.runtime.status = "Cancelled";
    demoState.runtime.currentTaskId = null;
    demoState.runtime.log.push("Demo render cancelled.");
    return demoJson({ ok: true });
  }
  return demoJson({ error: "This action is unavailable in demo mode." }, 404);
};
