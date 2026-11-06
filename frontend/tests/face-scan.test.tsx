import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
const mocks = vi.hoisted(() => ({
  loadFaceModels: vi.fn(),
  openCamera: vi.fn(),
  detectFace: vi.fn(),
}));
vi.mock("../src/face", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/face")>()),
  ...mocks,
}));
import FaceScan from "../src/FaceScan";
import { ApiError } from "../src/api";
import { consistent, distance } from "../src/face";

const stop = vi.fn();
let frame = 0;
// Synthetic descriptors: one stable "person" plus tiny per-frame noise.
const person = Array.from({ length: 128 }, (_, i) => Math.sin(i + 1) / 12);
function face(width = 320, base = person) {
  frame++;
  return {
    detection: { score: 0.9, box: { width } },
    descriptor: Float32Array.from(
      base,
      (x, i) => x + Math.sin(frame * 31 + i) * 0.004,
    ),
  };
}
beforeEach(() => {
  vi.resetAllMocks();
  frame = 0;
  mocks.loadFaceModels.mockResolvedValue({});
  mocks.openCamera.mockResolvedValue({ getTracks: () => [{ stop }] });
  mocks.detectFace.mockImplementation(async () => face());
  vi.spyOn(HTMLMediaElement.prototype, "readyState", "get").mockReturnValue(4);
  vi.spyOn(HTMLVideoElement.prototype, "videoWidth", "get").mockReturnValue(
    640,
  );
  HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
});
afterEach(() => vi.restoreAllMocks());

it("collects distinct frames of one face and submits only descriptors", async () => {
  const onCapture = vi.fn().mockResolvedValue(undefined);
  render(<FaceScan mode="verify" onCapture={onCapture} />);
  await screen.findByText("Verified", undefined, { timeout: 4000 });
  expect(onCapture).toHaveBeenCalledTimes(1);
  const samples: number[][] = onCapture.mock.calls[0][0];
  expect(samples).toHaveLength(3);
  expect(samples.every((s) => s.length === 128)).toBe(true);
  expect(distance(samples[0], samples[1])).toBeGreaterThan(0);
  expect(stop).toHaveBeenCalled(); // camera is released after success
});

it("enrolls five consistent samples", async () => {
  const onCapture = vi.fn().mockResolvedValue(undefined);
  render(<FaceScan mode="enroll" onCapture={onCapture} />);
  await screen.findByText("Face sign-in is set up", undefined, {
    timeout: 5000,
  });
  const samples: number[][] = onCapture.mock.calls[0][0];
  expect(samples).toHaveLength(5);
  expect(consistent(samples)).toBe(true);
});

it("shows a rejected face and lets the person scan again", async () => {
  const onCapture = vi
    .fn()
    .mockRejectedValueOnce(new ApiError(401, "face_not_recognized"))
    .mockResolvedValueOnce(undefined);
  render(<FaceScan mode="verify" onCapture={onCapture} />);
  await screen.findByText("Face not recognized", undefined, { timeout: 4000 });
  expect(screen.getByText(/did not match this account/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /Scan again/ }));
  await screen.findByText("Verified", undefined, { timeout: 4000 });
  expect(onCapture).toHaveBeenCalledTimes(2);
});

it("asks the person to move closer instead of scanning a small face", async () => {
  mocks.detectFace.mockImplementation(async () => face(60));
  const onCapture = vi.fn();
  render(<FaceScan mode="verify" onCapture={onCapture} />);
  await screen.findByText("Move a little closer");
  await new Promise((r) => setTimeout(r, 1300));
  expect(onCapture).not.toHaveBeenCalled();
});

it("explains a blocked camera and stops the camera when closed", async () => {
  mocks.openCamera.mockRejectedValueOnce(
    Object.assign(new Error("denied"), { name: "NotAllowedError" }),
  );
  mocks.detectFace.mockResolvedValue(null); // no face in view after the retry
  const { unmount } = render(<FaceScan mode="verify" onCapture={vi.fn()} />);
  await screen.findByText(/Camera access is blocked/);
  await userEvent.click(screen.getByRole("button", { name: /Scan again/ }));
  await screen.findByText("Look at the camera");
  unmount();
  await waitFor(() => expect(stop).toHaveBeenCalled());
});
