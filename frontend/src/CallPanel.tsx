import { useEffect, useState } from "react";
import { Phone, PhoneOff, Send } from "lucide-react";
import { api, date, short } from "./api";
import {
  Badge,
  Empty,
  ErrorBox,
  Field,
  Loading,
  Panel,
  Refresh,
  useAction,
  useResource,
} from "./ui";
import type { Call, Identity, Page, Turn } from "./types";
import PhoneVerification from "./PhoneVerification";
export default function CallPanel(props: {
  callId: string;
  identity: Identity;
  onChange?: () => void;
}) {
  return props.identity.booth_mode ? (
    <PhoneVerification callId={props.callId} onChange={props.onChange} />
  ) : (
    <StandardCallPanel {...props} />
  );
}
function StandardCallPanel({
  callId,
  identity,
  onChange,
}: {
  callId: string;
  identity: Identity;
  onChange?: () => void;
}) {
  const [rev, setRev] = useState(0),
    [text, setText] = useState("");
  const call = useResource<Call>("/calls/" + callId, rev),
    turns = useResource<Page<Turn>>("/calls/" + callId + "/turns", rev),
    audio = useResource<Page<{ id: string }>>(
      "/calls/" + callId + "/audio",
      rev,
    );
  const refresh = () => setRev((x) => x + 1);
  const action = useAction(() => {
    refresh();
    onChange?.();
  });
  useEffect(() => {
    const id = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, 15000);
    return () => clearInterval(id);
  }, [callId]);
  const c = call.data;
  const participant = identity.role === "participant";
  const live = c && ["RINGING", "ACTIVE"].includes(c.state);
  async function transition(operation: string) {
    if (c)
      await action.run(
        () =>
          api("/calls/" + callId + "/transition", {
            method: "POST",
            body: { expected_version: c.version, operation },
          }),
        "Call updated.",
      );
  }
  return (
    <Panel
      title={"Verification call · " + short(callId)}
      aside={<Refresh onClick={refresh} />}
    >
      <ErrorBox error={call.error} />
      {!c ? (
        call.loading && <Loading />
      ) : (
        <div className="call-content" dir={c.language === "ar" ? "rtl" : "ltr"}>
          <div className="call-header">
            <span className="call-symbol">
              <Phone size={22} />
            </span>
            <div>
              <strong>
                {c.language === "ar"
                  ? "مكالمة التحقق التجريبية"
                  : "Simulated bank verification"}
              </strong>
              <small>
                {c.language === "ar" ? "العربية" : "English"} · Expires{" "}
                {date(c.expires_at)}
              </small>
            </div>
            <Badge value={c.state} />
          </div>
          <p className="muted">
            Synthetic scenario. Never enter a PIN, password, OTP, or real
            financial information.
          </p>
          {action.feedback}
          <fieldset disabled={action.busy}>
            <div className="button-row">
              {participant && c.state === "RINGING" && (
                <>
                  <button onClick={() => transition("answer")}>
                    <Phone size={16} />
                    Answer call
                  </button>
                  <button
                    className="danger secondary"
                    onClick={() => transition("decline")}
                  >
                    <PhoneOff size={16} />
                    Decline
                  </button>
                </>
              )}
              {live && c.state === "ACTIVE" && (
                <button
                  className="secondary danger"
                  onClick={() => transition("end")}
                >
                  <PhoneOff size={16} />
                  End call
                </button>
              )}
            </div>
          </fieldset>
          <ErrorBox error={turns.error} />
          <div
            className="transcript"
            aria-label="Call transcript"
            aria-live="polite"
          >
            {turns.data?.items.length ? (
              turns.data.items.map((t) => (
                <div key={t.id} className={"bubble " + t.speaker}>
                  <small>
                    {t.speaker === "bank" ? "Rasid verification" : "Customer"}
                  </small>
                  <p dir="auto">{t.text}</p>
                </div>
              ))
            ) : (
              <p className="muted">
                The conversation appears when the call is answered.
              </p>
            )}
          </div>
          <ErrorBox error={audio.error} />
          {audio.data?.items.map((clip) => (
            <audio
              key={clip.id}
              controls
              preload="none"
              src={"/api/v1/calls/" + callId + "/audio/" + clip.id}
            >
              Audio playback is not supported.
            </audio>
          ))}
          {participant && c.state === "ACTIVE" && (
            <>
              <div className="verification-options">
                <p>Do you recognize this transaction?</p>
                <fieldset disabled={action.busy}>
                  {[
                    ["AUTHORIZED", "Yes, I authorized it"],
                    ["NOT_AUTHORIZED", "No, I did not"],
                    ["UNSURE", "I am not sure"],
                  ].map(([response, caption]) => (
                    <button
                      key={response}
                      className="secondary"
                      onClick={() =>
                        action.run(
                          () =>
                            api("/calls/" + callId + "/verification", {
                              method: "POST",
                              body: { expected_version: c.version, response },
                            }),
                          "Response recorded for the analyst.",
                        )
                      }
                    >
                      {caption}
                    </button>
                  ))}
                </fieldset>
              </div>
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  if (
                    await action.run(
                      () =>
                        api("/calls/" + callId + "/turns", {
                          method: "POST",
                          body: { expected_version: c.version, text },
                        }),
                      "Message recorded.",
                    )
                  )
                    setText("");
                }}
              >
                <fieldset disabled={action.busy}>
                  {c.local_transcription_enabled && (
                    <Field label="Transcribe a short recording (up to 15 seconds, 64 KB)">
                      <input
                        type="file"
                        accept="audio/*"
                        onChange={async (e) => {
                          const file = e.target.files?.[0];
                          e.target.value = "";
                          if (!file) return;
                          await action.run(async () => {
                            if (file.size > 65536)
                              throw new Error(
                                "Choose a recording smaller than 64 KB.",
                              );
                            const bytes = new Uint8Array(
                              await file.arrayBuffer(),
                            );
                            const result = await api<{ text: string }>(
                              "/calls/" + callId + "/transcribe",
                              {
                                method: "POST",
                                signal: AbortSignal.timeout(55000),
                                body: {
                                  expected_version: c.version,
                                  audio_base64: btoa(
                                    String.fromCharCode(...bytes),
                                  ),
                                },
                              },
                            );
                            setText(result.text);
                          }, "Transcription ready. Review the text, then send it.");
                        }}
                      />
                    </Field>
                  )}
                  <Field label="Additional details">
                    <textarea
                      required
                      maxLength={2000}
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                      placeholder={
                        c.language === "ar"
                          ? "اكتب رسالتك هنا…"
                          : "Write your reply…"
                      }
                    />
                  </Field>
                  <button type="submit">
                    <Send size={16} />
                    Send reply
                  </button>
                </fieldset>
              </form>
            </>
          )}
        </div>
      )}
    </Panel>
  );
}
export function CustomerCalls({ identity }: { identity: Identity }) {
  const [rev, setRev] = useState(0),
    [selected, setSelected] = useState(""),
    [cursor, setCursor] = useState("");
  const calls = useResource<Page<Call>>(
    "/calls?limit=30" + (cursor ? "&after=" + cursor : ""),
    rev,
  );
  useEffect(() => {
    const id = setInterval(() => {
      if (document.visibilityState === "visible") setRev((x) => x + 1);
    }, 15000);
    return () => clearInterval(id);
  }, []);
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">CUSTOMER VERIFICATION</div>
          <h1>Your verification calls</h1>
          <p>Review and respond to simulated transaction checks.</p>
        </div>
        <Refresh onClick={() => setRev((x) => x + 1)} />
      </div>
      <ErrorBox error={calls.error} />
      {calls.loading && !calls.data ? (
        <Loading />
      ) : (
        <div className="split-layout">
          <Panel title="Call inbox">
            {calls.data?.items.length ? (
              calls.data.items.map((c) => (
                <button
                  className={
                    "inbox-item " + (selected === c.id ? "selected" : "")
                  }
                  key={c.id}
                  onClick={() => setSelected(c.id)}
                >
                  <Phone size={18} />
                  <span>
                    <strong>Verification {short(c.id)}</strong>
                    <small>{c.language === "ar" ? "العربية" : "English"}</small>
                  </span>
                  <Badge value={c.state} />
                </button>
              ))
            ) : (
              <Empty title="No verification calls">
                Your assigned calls will appear here automatically.
              </Empty>
            )}
            <div className="button-row">
              {cursor && (
                <button className="text-button" onClick={() => setCursor("")}>
                  First page
                </button>
              )}
              {calls.data?.next_cursor && (
                <button
                  className="secondary"
                  onClick={() => setCursor(String(calls.data!.next_cursor))}
                >
                  Next page
                </button>
              )}
            </div>
          </Panel>
          {selected ? (
            <CallPanel key={selected} callId={selected} identity={identity} />
          ) : (
            <Empty title="Select a call">
              Only calls assigned to your account are visible.
            </Empty>
          )}
        </div>
      )}
    </>
  );
}
