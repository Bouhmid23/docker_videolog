import type { WebPoseResult } from "../types";

interface VisionModule {
  FilesetResolver: {
    forVisionTasks(wasmUrl: string): Promise<unknown>;
  };
  PoseLandmarker: {
    createFromOptions(
      vision: unknown,
      options: unknown
    ): Promise<{
      detectForVideo(
        video: HTMLVideoElement,
        timestampMs: number
      ): {
        worldLandmarks?: Array<Array<{ x: number; y: number; z: number }>>;
        landmarks?: Array<Array<{ x: number; y: number; z: number; visibility?: number }>>;
      };
    }>;
  };
}

let detectorPromise: Promise<
  Awaited<ReturnType<VisionModule["PoseLandmarker"]["createFromOptions"]>>
> | null = null;
let lastTimestamp = 0;

export function getWebPoseConfig() {
  const q = new URLSearchParams(window.location.search);
  const wanted = q.get("webmodel");
  const model =
    wanted === "lite" || wanted === "full" || wanted === "heavy" ? wanted : "full";
  const hz = Number(q.get("webhz")) || 20;
  return {
    enabled: q.get("pose") === "web",
    model,
    minIntervalMs: 1000 / hz,
  };
}

async function getDetector(model: string) {
  if (!detectorPromise) {
    detectorPromise = (async () => {
      const modUrl =
        "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1/vision_bundle.mjs";
      const visionMod = (await import(/* @vite-ignore */ modUrl)) as VisionModule;
      const vision = await visionMod.FilesetResolver.forVisionTasks(
        "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1/wasm"
      );
      const options = {
        baseOptions: {
          modelAssetPath: `https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_${model}/float16/latest/pose_landmarker_${model}.task`,
          delegate: "GPU",
        },
        runningMode: "VIDEO",
        numPoses: 1,
      };
      try {
        return await visionMod.PoseLandmarker.createFromOptions(vision, options);
      } catch (err) {
        console.warn("MediaPipe GPU indisponible, passage au CPU :", err);
        return visionMod.PoseLandmarker.createFromOptions(vision, {
          ...options,
          baseOptions: { ...options.baseOptions, delegate: "CPU" },
        });
      }
    })();
  }
  return detectorPromise;
}

export async function startWebPoseDetection(
  video: HTMLVideoElement,
  onResult: (r: WebPoseResult) => void,
  onError: (err: Error) => void
): Promise<() => void> {
  const cfg = getWebPoseConfig();
  const detector = await getDetector(cfg.model);
  let active = true;
  let handle = 0;
  let lastVideoTime = -1;
  let lastAt = 0;

  const videoWithCallback = video as HTMLVideoElement & {
    requestVideoFrameCallback?: (cb: (now: number) => void) => number;
    cancelVideoFrameCallback?: (id: number) => void;
  };

  const step = (now: number) => {
    if (!active) return;
    if (
      video.readyState >= 2 &&
      video.videoWidth > 0 &&
      video.currentTime !== lastVideoTime &&
      now - lastAt >= cfg.minIntervalMs * 0.85
    ) {
      lastVideoTime = video.currentTime;
      lastAt = now;
      try {
        lastTimestamp = Math.max(performance.now(), lastTimestamp + 0.1);
        const t0 = performance.now();
        const res = detector.detectForVideo(video, lastTimestamp);
        const world = res.worldLandmarks?.[0];
        const image = res.landmarks?.[0];
        if (world && image) {
          onResult({
            world: world.map((p) => [p.x, p.y, p.z]),
            image: image.map((p) => [p.x, p.y, p.z]),
            vis: image.map((p) => p.visibility ?? 0),
            detectMs: performance.now() - t0,
          });
        }
      } catch (e) {
        active = false;
        onError(e instanceof Error ? e : new Error(String(e)));
        return;
      }
    }
    handle =
      typeof videoWithCallback.requestVideoFrameCallback === "function"
        ? videoWithCallback.requestVideoFrameCallback(step)
        : requestAnimationFrame(step);
  };

  handle =
    typeof videoWithCallback.requestVideoFrameCallback === "function"
      ? videoWithCallback.requestVideoFrameCallback(step)
      : requestAnimationFrame(step);

  return () => {
    active = false;
    if (
      typeof videoWithCallback.cancelVideoFrameCallback === "function" &&
      typeof videoWithCallback.requestVideoFrameCallback === "function"
    ) {
      videoWithCallback.cancelVideoFrameCallback(handle);
    } else {
      cancelAnimationFrame(handle);
    }
  };
}
