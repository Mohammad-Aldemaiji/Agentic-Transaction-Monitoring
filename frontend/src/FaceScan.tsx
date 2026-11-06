import { useEffect, useRef, useState } from "react";
import { RotateCcw } from "lucide-react";
import { ApiError } from "./api";
import {
  ENROLL_SAMPLES,
  MIN_FACE,
  VERIFY_SAMPLES,
  cameraMessage,
  consistent,
  detectFace,
  distance,
  loadFaceModels,
  openCamera,
} from "./face";

type Phase =
  | "starting"
  | "searching"
  | "closer"
  | "scanning"
  | "checking"
  | "success"
  | "failed"
  | "error";
type FaceApi = Awaited<ReturnType<typeof loadFaceModels>>;

const MIN_SCAN_MS = 1100; // the scan animation always plays at least this long
const LOST_FACE_MS = 1200; // no face for this long restarts the scan
const FRAME_GAP_MS = 60;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Camera scan for the second sign-in step. It collects face descriptors from
 * several frames and hands them to `onCapture`, which submits them to the server.
 * `onCapture` resolves when the server accepts the face and rejects otherwise.
 */
export default function FaceScan({
  mode,
  onCapture,
}: {
  mode: "verify" | "enroll";
  onCapture: (samples: number[][]) => Promise<void>;
}) {
  const needed = mode === "enroll" ? ENROLL_SAMPLES : VERIFY_SAMPLES;
  const gap = mode === "enroll" ? 300 : 120; // spread samples over distinct frames
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const faceApi = useRef<FaceApi | null>(null);
  const loop = useRef(0);
  const scan = useRef({
    samples: [] as number[][],
    start: 0,
    last: 0,
    seen: 0,
  });
  const [phase, setPhase] = useState<Phase>("starting");
  const [count, setCount] = useState(0);
  const [problem, setProblem] = useState("");
  const [hint, setHint] = useState("");

  function reset() {
    scan.current = { samples: [], start: 0, last: 0, seen: performance.now() };
    setCount(0);
  }

  function stopCamera() {
    loop.current++;
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
    if (video.current) video.current.srcObject = null;
  }

  async function submit(samples: number[][]) {
    setPhase("checking");
    try {
      await onCapture(samples);
      setPhase("success");
      stopCamera();
    } catch (error) {
      setProblem(
        error instanceof Error
          ? error.message
          : "Your face could not be verified.",
      );
      setPhase(
        error instanceof ApiError && error.code === "face_not_recognized"
          ? "failed"
          : "error",
      );
      reset();
    }
  }

  function frame(result: Awaited<ReturnType<typeof detectFace>>) {
    const now = performance.now();
    const state = scan.current;
    const width = video.current?.videoWidth ?? 0;
    if (!result) {
      if (now - state.seen > LOST_FACE_MS && state.samples.length) reset();
      if (now - state.seen > LOST_FACE_MS) setPhase("searching");
      return true;
    }
    state.seen = now;
    if (width && result.detection.box.width < width * MIN_FACE) {
      if (state.samples.length) reset();
      setPhase("closer");
      return true;
    }
    if (!state.start) state.start = now;
    setPhase("scanning");
    const descriptor = Array.from(result.descriptor);
    const previous = state.samples[state.samples.length - 1];
    if (
      now - state.last >= gap &&
      (!previous || distance(previous, descriptor) > 1e-6)
    ) {
      state.samples.push(descriptor);
      state.last = now;
      setCount(state.samples.length);
    }
    if (state.samples.length < needed || now - state.start < MIN_SCAN_MS)
      return true;
    if (mode === "enroll" && !consistent(state.samples)) {
      setHint("Keep only your face in the frame and hold still.");
      reset();
      return true;
    }
    void submit(state.samples.slice(0, needed));
    return false;
  }

  function startLoop() {
    const id = ++loop.current;
    reset();
    setProblem("");
    setPhase("searching");
    void (async () => {
      while (id === loop.current) {
        const v = video.current;
        if (faceApi.current && v && v.readyState >= 2 && v.videoWidth) {
          let keepGoing = true;
          try {
            const result = await detectFace(faceApi.current, v);
            if (id !== loop.current) return;
            keepGoing = frame(result);
          } catch {
            await sleep(500);
          }
          if (!keepGoing) return;
        }
        await sleep(FRAME_GAP_MS);
      }
    })();
  }

  async function start() {
    const id = ++loop.current;
    const fail = (message: string) => {
      if (id !== loop.current) return;
      setProblem(message);
      setPhase("error");
    };
    setPhase("starting");
    setProblem("");
    // Load the models while the camera starts; show the preview as soon as it is live.
    const models = loadFaceModels();
    models.catch(() => undefined);
    let camera: MediaStream;
    try {
      camera = stream.current ?? (await openCamera());
    } catch (error) {
      return fail(cameraMessage(error));
    }
    if (id !== loop.current) {
      if (camera !== stream.current)
        camera.getTracks().forEach((t) => t.stop());
      return;
    }
    stream.current = camera;
    const v = video.current;
    if (v && v.srcObject !== camera) {
      v.srcObject = camera;
      try {
        await v.play();
      } catch {
        // Autoplay can be refused; the muted preview still starts on most browsers.
      }
    }
    try {
      faceApi.current = await models;
    } catch {
      return fail(
        "Face recognition could not load. Reload the page and try again.",
      );
    }
    if (id === loop.current) startLoop();
  }

  useEffect(() => {
    void start();
    return stopCamera; // runs once per mount; a scan's mode never changes
  }, []);

  const [title, detail] = {
    starting: ["Starting camera…", "Loading face recognition on this device"],
    searching: ["Look at the camera", "Center your face in the frame"],
    closer: ["Move a little closer", "Your face is too small to scan"],
    scanning: [
      mode === "enroll" ? "Capturing your face…" : "Scanning…",
      hint || "Hold still",
    ],
    checking: [
      "Checking…",
      mode === "enroll"
        ? "Saving your face sign-in"
        : "Matching with your enrolled face",
    ],
    success: [
      mode === "enroll" ? "Face sign-in is set up" : "Verified",
      "Opening your workspace",
    ],
    failed: ["Face not recognized", problem],
    error: ["Verification stopped", problem],
  }[phase];

  return (
    <div className="face-scan" data-phase={phase}>
      <div className="face-frame">
        <video ref={video} autoPlay muted playsInline aria-hidden="true" />
        <div className="face-sweep" aria-hidden="true" />
        <svg className="face-brackets" viewBox="0 0 100 100" aria-hidden="true">
          <path d="M4 30V22Q4 4 22 4H30" />
          <path d="M96 30V22Q96 4 78 4H70" />
          <path d="M4 70V78Q4 96 22 96H30" />
          <path d="M96 70V78Q96 96 78 96H70" />
        </svg>
        <svg className="face-check" viewBox="0 0 52 52" aria-hidden="true">
          <circle cx="26" cy="26" r="24" />
          <path d="M14 27l9 9 16-18" />
        </svg>
      </div>
      <div
        className="face-progress"
        role="progressbar"
        aria-label="Face samples captured"
        aria-valuemin={0}
        aria-valuemax={needed}
        aria-valuenow={Math.min(count, needed)}
      >
        {Array.from({ length: needed }, (_, i) => (
          <i key={i} className={i < count ? "done" : ""} />
        ))}
      </div>
      <div className="face-status" role="status" aria-live="polite">
        <strong>{title}</strong>
        {detail && <span>{detail}</span>}
      </div>
      {(phase === "failed" || phase === "error") && (
        <button
          type="button"
          className="secondary wide"
          onClick={() =>
            stream.current && faceApi.current ? startLoop() : void start()
          }
        >
          <RotateCcw size={16} />
          Scan again
        </button>
      )}
    </div>
  );
}
