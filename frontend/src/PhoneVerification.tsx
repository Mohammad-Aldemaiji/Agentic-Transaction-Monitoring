import { useEffect, useRef, useState } from "react";
import { Phone, PhoneOff, Volume2 } from "lucide-react";
import { api } from "./api";
import { ErrorBox, Loading, Panel, useAction, useResource } from "./ui";
import type { Call, Page, Turn } from "./types";

export default function PhoneVerification({
  callId,
  onChange,
}: {
  callId: string;
  onChange?: () => void;
}) {
  const [revision, setRevision] = useState(0),
    [audioError, setAudioError] = useState<unknown>(null);
  const call = useResource<Call>("/calls/" + callId, revision);
  const turns = useResource<Page<Turn>>(
    "/calls/" + callId + "/turns",
    revision,
  );
  const clips = useResource<Page<{ id: string }> & { status?: string }>(
    "/calls/" + callId + "/audio",
    revision,
  );
  const [audioAttempt, setAudioAttempt] = useState(0);
  const [audioLoading, setAudioLoading] = useState(false);
  const changed = useRef(onChange);
  changed.current = onChange;
  const notifiedTerminal = useRef("");
  const context = useRef<AudioContext | null>(null),
    playing = useRef<AudioBufferSourceNode | null>(null);
  const buffer = useRef<AudioBuffer | null>(null),
    played = useRef("");
  const action = useAction(() => {
    setRevision((r) => r + 1);
    onChange?.();
  });
  const state = call.data?.state;
  useEffect(() => {
    if (
      state &&
      !["RINGING", "ACTIVE"].includes(state) &&
      notifiedTerminal.current !== state
    ) {
      notifiedTerminal.current = state;
      playing.current?.stop();
      changed.current?.();
    }
  }, [state]);
  useEffect(() => {
    if (state !== "RINGING" && state !== "ACTIVE") return;
    const timer = setInterval(() => setRevision((r) => r + 1), 2000);
    return () => clearInterval(timer);
  }, [state]);
  useEffect(
    () => () => {
      playing.current?.stop();
      void context.current?.close();
    },
    [],
  );
  async function play() {
    if (!context.current) context.current = new AudioContext();
    await context.current.resume();
    if (!buffer.current) return;
    playing.current?.stop();
    const source = context.current.createBufferSource();
    source.buffer = buffer.current;
    source.connect(context.current.destination);
    source.start();
    playing.current = source;
    setAudioError(null);
  }
  async function retryAudio() {
    if (buffer.current) return play();
    if (!context.current) context.current = new AudioContext();
    await context.current.resume();
    setAudioAttempt((n) => n + 1);
  }
  const clipId = clips.data?.items[0]?.id;
  useEffect(() => {
    if (!clipId || state !== "ACTIVE" || played.current === clipId) return;
    const controller = new AbortController();
    let live = true;
    const timeout = setTimeout(() => controller.abort(), 15000);
    setAudioLoading(true);
    setAudioError(null);
    void (async () => {
      try {
        const response = await fetch(
          `/api/v1/calls/${callId}/audio/${clipId}`,
          { credentials: "same-origin", signal: controller.signal },
        );
        if (!response.ok)
          throw new Error(
            "Voice instructions could not be loaded. Please retry playback.",
          );
        if (!context.current) context.current = new AudioContext();
        const decoded = await context.current.decodeAudioData(
          await response.arrayBuffer(),
        );
        if (!live) return;
        buffer.current = decoded;
        played.current = clipId;
        setAudioError(null);
        if (context.current.state === "running") await play();
      } catch (error) {
        if (live)
          setAudioError(
            controller.signal.aborted
              ? new Error(
                  "Voice download timed out. Retry the audio or use the transcript.",
                )
              : error,
          );
      } finally {
        clearTimeout(timeout);
        if (live) setAudioLoading(false);
      }
    })();
    return () => {
      live = false;
      clearTimeout(timeout);
      controller.abort();
    };
  }, [clipId, state, callId, audioAttempt]);
  async function respond(
    reply: "answer" | "confirmed" | "denied" | "unavailable",
  ) {
    if (!call.data) return;
    if (reply === "answer") {
      try {
        if (!context.current) context.current = new AudioContext();
        await context.current.resume();
      } catch {
        setAudioError(new Error("Use Play instructions to enable audio."));
      }
    } else {
      playing.current?.stop();
    }
    await action.run(
      () =>
        api(`/booth/calls/${callId}/customer-reply`, {
          method: "POST",
          body: { expected_version: call.data!.version, reply },
        }),
      reply === "answer"
        ? "Call connected."
        : reply === "unavailable"
          ? "Customer unavailable."
          : "Customer response recorded.",
    );
  }
  if (!call.data)
    return (
      <>
        <ErrorBox error={call.error} />
        <Loading />
      </>
    );
  const ringing = state === "RINGING",
    active = state === "ACTIVE";
  const status = ringing
    ? "Incoming verification call"
    : active
      ? "Connected"
      : state === "TIMED_OUT"
        ? "Customer unavailable / timed out"
        : state === "DECLINED"
          ? "Customer unavailable"
          : "Call ended";
  return (
    <Panel title="Customer verification">
      <div className="phone-layout">
        <div className={"phone-device " + (ringing ? "is-ringing" : "")}>
          <div className="phone-speaker" />
          <span className="phone-bank">RASID BANK</span>
          <div className="phone-avatar">
            <Phone size={32} />
          </div>
          <h3>Payment verification</h3>
          <p className="phone-status" role="status">
            {status}
          </p>
          {ringing && (
            <div className="phone-call-actions">
              <button
                className="phone-accept"
                disabled={action.busy}
                onClick={() => respond("answer")}
              >
                <Phone />
                Accept
              </button>
              <button
                className="phone-decline"
                disabled={action.busy}
                onClick={() => respond("unavailable")}
              >
                <PhoneOff />
                Unavailable
              </button>
            </div>
          )}
          {active && (
            <>
              <p>
                Press <strong>1</strong> if you made this payment.
                <br />
                Press <strong>2</strong> if you did not.
              </p>
              <button
                className="phone-audio"
                disabled={!clipId || audioLoading}
                onClick={() => void retryAudio().catch(setAudioError)}
              >
                <Volume2 size={16} />
                {audioLoading
                  ? "Loading voice instructions…"
                  : audioError && clipId
                    ? "Retry audio"
                    : clipId
                      ? "Play instructions"
                      : clips.data?.status === "FAILED"
                        ? "Voice unavailable — use transcript"
                        : "Preparing voice instructions…"}
              </button>
              <div className="phone-keypad" aria-label="Verification keypad">
                {[1, 2, 3, 4, 5, 6, 7, 8, 9, "*", 0, "#"].map((key) => (
                  <button
                    key={key}
                    disabled={action.busy || (key !== 1 && key !== 2)}
                    aria-label={
                      key === 1
                        ? "1 — Confirm payment"
                        : key === 2
                          ? "2 — Deny payment"
                          : String(key)
                    }
                    onClick={() => respond(key === 1 ? "confirmed" : "denied")}
                  >
                    {key}
                    <small>
                      {key === 1 ? "CONFIRM" : key === 2 ? "DENY" : " "}
                    </small>
                  </button>
                ))}
              </div>
            </>
          )}
          {!ringing && !active && (
            <PhoneOff className="phone-ended" size={32} />
          )}
          <div className="phone-home" />
        </div>
        <div className="phone-evidence">
          {action.feedback}
          <ErrorBox error={call.error} />
          <ErrorBox error={audioError} />
          <ErrorBox error={clips.error} />
          <h3>Call transcript</h3>
          <p className="muted">
            The response is evidence for the analyst. It does not approve a bank
            action.
          </p>
          <ErrorBox error={turns.error} />
          <div className="transcript">
            {turns.data?.items.map((turn) => (
              <div key={turn.id} className={"bubble " + turn.speaker}>
                <small>
                  {turn.speaker === "bank" ? "Rasid verification" : "Customer"}
                </small>
                <p>{turn.text}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Panel>
  );
}
