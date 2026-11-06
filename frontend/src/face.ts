// Face detection in the browser with face-api.js (MIT, bundled under /face/).
// Only 128-number face descriptors leave this module; no image is uploaded or stored.
// The server stores enrolled descriptors and makes every match decision.

export const FACE_BASE = "/face";
export const MATCH_THRESHOLD = 0.5; // same value the server enforces
export const VERIFY_SAMPLES = 3;
export const ENROLL_SAMPLES = 5;
export const MIN_FACE = 0.2; // face width / video width; smaller means "move closer"

export interface Detection {
  descriptor: Float32Array;
  detection: { score: number; box: { width: number } };
}
interface FaceApi {
  nets: Record<
    "tinyFaceDetector" | "faceLandmark68Net" | "faceRecognitionNet",
    { loadFromUri(url: string): Promise<void> }
  >;
  TinyFaceDetectorOptions: new (options: {
    inputSize: number;
    scoreThreshold: number;
  }) => unknown;
  detectSingleFace(
    input: HTMLVideoElement,
    options: unknown,
  ): {
    withFaceLandmarks(): {
      withFaceDescriptor(): Promise<Detection | undefined>;
    };
  };
}
declare global {
  interface Window {
    faceapi?: FaceApi;
  }
}

let loading: Promise<FaceApi> | null = null;

function script(src: string) {
  return new Promise<void>((resolve, reject) => {
    const element = document.createElement("script");
    element.src = src;
    element.async = true;
    element.onload = () => resolve();
    element.onerror = () => reject(new Error("face library unavailable"));
    document.head.append(element);
  });
}

/** Loads the library and the three models once per page; retried after a failure. */
export function loadFaceModels(): Promise<FaceApi> {
  loading ??= (async () => {
    if (!window.faceapi) await script(FACE_BASE + "/face-api.min.js");
    const api = window.faceapi;
    if (!api) throw new Error("face library unavailable");
    const models = FACE_BASE + "/models";
    await Promise.all([
      api.nets.tinyFaceDetector.loadFromUri(models),
      api.nets.faceLandmark68Net.loadFromUri(models),
      api.nets.faceRecognitionNet.loadFromUri(models),
    ]);
    return api;
  })().catch((error) => {
    loading = null;
    throw error;
  });
  return loading;
}

export function openCamera(): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia)
    return Promise.reject(
      Object.assign(new Error("camera unavailable"), { name: "NotFoundError" }),
    );
  return navigator.mediaDevices.getUserMedia({
    audio: false,
    video: {
      facingMode: "user",
      width: { ideal: 640 },
      height: { ideal: 480 },
    },
  });
}

export async function detectFace(
  api: FaceApi,
  video: HTMLVideoElement,
): Promise<Detection | null> {
  const result = await api
    .detectSingleFace(
      video,
      new api.TinyFaceDetectorOptions({ inputSize: 320, scoreThreshold: 0.5 }),
    )
    .withFaceLandmarks()
    .withFaceDescriptor();
  return result ?? null;
}

export function distance(a: ArrayLike<number>, b: ArrayLike<number>) {
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += (a[i] - b[i]) ** 2;
  return Math.sqrt(sum);
}

/** Enrollment frames must show one person: every pair is under the match threshold. */
export function consistent(samples: number[][]) {
  return samples.every((a, i) =>
    samples.slice(i + 1).every((b) => distance(a, b) < MATCH_THRESHOLD),
  );
}

export function cameraMessage(error: unknown) {
  const name = error instanceof Error ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError")
    return "Camera access is blocked. Allow the camera using the icon in the address bar, then try again.";
  if (name === "NotFoundError" || name === "OverconstrainedError")
    return "No camera was found. Connect a camera, then try again.";
  if (name === "NotReadableError")
    return "The camera is in use by another app. Close it, then try again.";
  return "The camera could not be started. Try again.";
}
