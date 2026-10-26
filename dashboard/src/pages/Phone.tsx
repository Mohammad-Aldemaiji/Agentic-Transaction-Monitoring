import { useEffect, useRef, useState } from "react";
import { PhoneState, api, ago, usePoll } from "../api";

/**
 * The customer's phone for the demo. Open it in a second window: the agent's
 * verification call rings here. The agent's voice is synthesized on the server
 * (Piper); the customer's answer is recorded here and transcribed on the server
 * (faster-whisper). The browser's own speech recognition is not used, because
 * Chrome and Edge send that audio to their cloud.
 */
export default function Phone() {
  const { data: p, reload } = usePoll<PhoneState>("/api/phone", 700);
  const [recording, setRecording] = useState(false);
  const [hint, setHint] = useState("");
  const [typed, setTyped] = useState("");
  const [speaking, setSpeaking] = useState(false);
  const played = useRef<{ call: number; n: number }>({ call: -1, n: 0 });
  const queue = useRef<Promise<void>>(Promise.resolve());
  const rec = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const ctx = useRef<AudioContext | null>(null);
  const ring = useRef<(() => void) | null>(null);
  const autoListened = useRef<string>("");

  // Ring tone while ringing.
  useEffect(() => {
    if (p?.state === "ringing" && !ring.current) ring.current = playRing(ctx);
    if (p?.state !== "ringing" && ring.current) { ring.current(); ring.current = null; }
  }, [p?.state]);

  // Speak each new agent line once, in order.
  useEffect(() => {
    if (!p) return;
    if (played.current.call !== p.call_no) played.current = { call: p.call_no, n: 0 };
    const fresh = p.lines.slice(played.current.n);
    played.current.n = p.lines.length;
    for (const line of fresh) {
      if (line.who !== "agent" || p.state === "ringing") continue;
      queue.current = queue.current.then(async () => {
        setSpeaking(true);
        await speak(line.text, line.audio, p.code);
        setSpeaking(false);
      });
    }
  }, [p]);

  // When the agent is waiting and has finished speaking, start listening by itself once per turn.
  useEffect(() => {
    if (!p?.waiting || speaking || recording) return;
    const turn = `${p.call_no}:${p.lines.length}`;
    if (autoListened.current === turn) return;
    autoListened.current = turn;
    queue.current.then(() => listen());
  });

  const answer = async (accept: boolean) => {
    ctx.current = ctx.current ?? new AudioContext();
    await api("/api/phone/answer", { accept }).catch(() => undefined);
    reload();
  };
  const hangUp = async () => {
    stopRecording();
    speechSynthesis.cancel();
    await api("/api/phone/hangup", {});
    reload();
  };
  const send = async (text: string) => {
    if (!text.trim()) return;
    stopRecording();
    try { await api("/api/phone/reply", { text }); setTyped(""); setHint(""); }
    catch (e) { setHint((e as Error).message); }
    reload();
  };

  async function listen() {
    if (rec.current) return;
    if (!navigator.mediaDevices || !window.MediaRecorder) { setHint("Recording isn't available here. Type the answer."); return; }
    try {
      stream.current = stream.current ?? await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch { setHint("Microphone blocked. Allow it in the address bar, or type the answer."); return; }
    const r = new MediaRecorder(stream.current);
    const chunks: Blob[] = [];
    rec.current = r;
    r.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    // Stop after 1.2 s of quiet following speech, 8 s of nothing, or 20 s in total.
    const ac = ctx.current ?? (ctx.current = new AudioContext());
    const src = ac.createMediaStreamSource(stream.current);
    const an = ac.createAnalyser();
    an.fftSize = 1024; src.connect(an);
    const buf = new Float32Array(an.fftSize);
    const t0 = Date.now();
    let spoke = false, quiet = Date.now();
    const tick = setInterval(() => {
      an.getFloatTimeDomainData(buf);
      const rms = Math.sqrt(buf.reduce((a, x) => a + x * x, 0) / buf.length);
      if (rms > 0.02) { spoke = true; quiet = Date.now(); }
      const age = Date.now() - t0;
      if ((spoke && Date.now() - quiet > 1200) || (!spoke && age > 8000) || age > 20000) r.stop();
    }, 100);
    r.onstop = async () => {
      clearInterval(tick); src.disconnect(); rec.current = null; setRecording(false);
      if (!spoke) { setHint("Didn't hear anything. Tap the microphone and speak, or type."); return; }
      setHint("Transcribing on the server…");
      try {
        const res = await fetch("/api/phone/transcribe", { method: "POST", headers: { "Content-Type": r.mimeType || "audio/webm" }, body: new Blob(chunks, { type: r.mimeType }) });
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail ?? res.status);
        if (d.text) await send(d.text); else setHint("Didn't catch that. Try again or type.");
      } catch (e) { setHint(`Transcription failed (${(e as Error).message}). Type the answer instead.`); }
    };
    r.start(250);
    setRecording(true);
    setHint("Listening…");
  }
  function stopRecording() {
    if (rec.current && rec.current.state !== "inactive") rec.current.stop();
  }

  const state = p?.state ?? "idle";
  return (
    <div className="phone-page">
      <div className="handset">
        <div className={`screen ${state}`}>
          <div className="status"><span>Customer's phone</span><span>{p?.account_id ? `…${p.account_id.slice(-4)}` : ""}</span></div>
          {state === "idle" && (
            <div className="idle"><h2>No calls</h2><p>When an analyst approves a verification call, it rings here.</p></div>
          )}
          {state !== "idle" && (
            <>
              <div className="who">
                <h2>Bank fraud team</h2>
                <p>{state === "ringing" ? "Incoming call" : state === "in_call" ? (speaking ? "Speaking…" : p?.waiting ? "Your turn" : "On call") : "Call ended"}</p>
              </div>
              {state !== "ringing" && (
                <div className="talk" dir="auto">
                  {p?.lines.map((l, i) => <div key={i} className={`l ${l.who}`} dir="auto">{l.text}</div>)}
                </div>
              )}
              <div className="controls">
                {state === "ringing" && (
                  <div className="round-row">
                    <button className="round decline" onClick={() => answer(false)}>Decline</button>
                    <button className="round accept" onClick={() => answer(true)}>Answer</button>
                  </div>
                )}
                {state === "in_call" && (
                  <>
                    <div className="round-row">
                      <button className={`round mic ${recording ? "on" : ""}`} disabled={!p?.waiting}
                        onClick={() => (recording ? stopRecording() : listen())} aria-label={recording ? "Stop recording" : "Speak"}>
                        {recording ? "Stop" : "Speak"}
                      </button>
                      <button className="round decline" onClick={hangUp}>Hang up</button>
                    </div>
                    <form className="typein" onSubmit={(e) => { e.preventDefault(); send(typed); }}>
                      <input type="text" dir="auto" placeholder="Or type the answer" value={typed} disabled={!p?.waiting} onChange={(e) => setTyped(e.target.value)} />
                      <button className="btn small" disabled={!p?.waiting || !typed.trim()}>Send</button>
                    </form>
                  </>
                )}
                {hint && <p className="hint" role="status">{hint}</p>}
              </div>
            </>
          )}
        </div>
      </div>
      <div className="notes">
        <h2>Messages from the bank</h2>
        {p?.notifications.length === 0 && <p className="hint">None yet.</p>}
        {p?.notifications.map((n) => (
          <div className="note" key={n.id}>{n.text}<span className="hint">Account …{n.account_id.slice(-4)}, {ago(n.at)}</span></div>
        ))}
      </div>
    </div>
  );
}

/** Play the server's WAV; without one, use a voice installed on this computer (never a network voice). */
function speak(text: string, audio: string | null, code: string): Promise<void> {
  if (audio) {
    return new Promise((res) => {
      const a = new Audio(audio);
      a.onended = () => res(); a.onerror = () => res();
      a.play().catch(() => res());
    });
  }
  const pre = code.slice(0, 2).toLowerCase();
  const v = speechSynthesis.getVoices().find((x) => x.localService && x.lang.toLowerCase().startsWith(pre));
  if (!v) return Promise.resolve();
  return new Promise((res) => {
    const u = new SpeechSynthesisUtterance(text);
    u.voice = v; u.lang = code;
    u.onend = () => res(); u.onerror = () => res();
    setTimeout(res, 1500 + text.length * 110);
    speechSynthesis.speak(u);
  });
}

function playRing(ctx: React.MutableRefObject<AudioContext | null>): () => void {
  try {
    ctx.current = ctx.current ?? new AudioContext();
    const ac = ctx.current;
    let on = true;
    const g = ac.createGain(); g.gain.value = 0; g.connect(ac.destination);
    const oscs = [440, 480].map((f) => { const o = ac.createOscillator(); o.frequency.value = f; o.connect(g); o.start(); return o; });
    const beat = () => { if (!on) return; g.gain.setValueAtTime(0.06, ac.currentTime); g.gain.setValueAtTime(0, ac.currentTime + 1.6); setTimeout(beat, 3500); };
    beat();
    return () => { on = false; oscs.forEach((o) => o.stop()); g.disconnect(); };
  } catch { return () => undefined; }
}
