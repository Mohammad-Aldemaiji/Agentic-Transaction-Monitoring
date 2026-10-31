import { useEffect, useRef, useState } from "react";
import { api, date, minorUnits, money } from "./api";
import {
  Badge,
  Details,
  Empty,
  ErrorBox,
  Field,
  Loading,
  Panel,
  Refresh,
  Title,
  useAction,
  useResource,
} from "./ui";
import type { Page, Transaction } from "./types";
import sampleTraffic from "./sampleTraffic.json";
export default function Transactions() {
  const [sampleOffset, setSampleOffset] = useState(0);
  const [rev, setRev] = useState(0),
    [cursor, setCursor] = useState(""),
    [selected, setSelected] = useState(""),
    [batch, setBatch] = useState("");
  const [form, setForm] = useState({
    external_id: "",
    account_id: "",
    amount: "",
    currency: "SAR",
    channel: "TRANSFER",
    occurred_at: "",
    card_id: "",
    device_id: "",
    destination_account_id: "",
  });
  const idempotency = useRef(crypto.randomUUID());
  const refresh = () => setRev((x) => x + 1);
  const list = useResource<Page<Transaction>>(
      "/transactions?limit=20" + (cursor ? "&after=" + cursor : ""),
      rev,
    ),
    processing = useResource<{
      status: string;
      case_id: string | null;
      job: { state: string; error_code: string | null } | null;
      scoring_configured: boolean;
    }>(selected ? "/transactions/" + selected + "/processing" : null, rev),
    action = useAction(refresh);
  useEffect(() => {
    if (!selected) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, 10000);
    return () => clearInterval(timer);
  }, [selected]);
  function change(key: keyof typeof form, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
    idempotency.current = crypto.randomUUID();
  }
  return (
    <>
      <Title
        eyebrow="TRANSACTION OPERATIONS"
        title="Transaction intake"
        actions={<Refresh onClick={refresh} />}
      >
        Submit synthetic transactions and follow their processing status.
      </Title>
      {action.feedback}
      <Panel title="Simulate incoming traffic">
        <div className="panel-body">
          <p>
            Send ten synthetic sample transactions through the normal processing
            queue. Original dates are preserved so the supervised model can use
            the matching sample features. Scoring still requires an active
            worker and an approved model.
          </p>
          <button
            disabled={action.busy || sampleOffset >= sampleTraffic.length}
            onClick={() =>
              action.run(async () => {
                const result = await api<{ items: Transaction[] }>(
                  "/transactions/batch",
                  {
                    method: "POST",
                    body: {
                      items: sampleTraffic.slice(
                        sampleOffset,
                        sampleOffset + 10,
                      ),
                    },
                  },
                );
                setSampleOffset((offset) => offset + 10);
                setCursor("");
                setSelected(result.items[0]?.id ?? "");
              }, "Sample batch accepted. Existing samples are reused without duplicates.")
            }
          >
            Simulate incoming traffic
          </button>
          <p className="muted">
            {sampleOffset} / {sampleTraffic.length} samples replayed this visit.
            Revisiting this screen reuses the same samples; it does not
            duplicate transactions.
          </p>
        </div>
      </Panel>
      <div className="case-columns">
        <Panel title="New transaction">
          <form
            className="panel-body"
            onSubmit={async (e) => {
              e.preventDefault();
              await action.run(async () => {
                const context = Object.fromEntries(
                  ["card_id", "device_id", "destination_account_id"]
                    .filter((k) => form[k as keyof typeof form])
                    .map((k) => [k, form[k as keyof typeof form]]),
                );
                const t = await api<Transaction>("/transactions", {
                  method: "POST",
                  idempotency: idempotency.current,
                  body: {
                    external_id: form.external_id,
                    account_id: form.account_id,
                    amount_minor: minorUnits(form.amount, form.currency),
                    currency: form.currency,
                    channel: form.channel,
                    occurred_at: new Date(form.occurred_at).toISOString(),
                    context,
                  },
                });
                setSelected(t.id);
              }, "Transaction accepted. Follow processing below.");
            }}
          >
            <fieldset disabled={action.busy}>
              <div className="form-grid">
                {[
                  ["external_id", "External transaction ID"],
                  ["account_id", "Synthetic account ID"],
                ].map(([key, caption]) => (
                  <Field label={caption} key={key}>
                    <input
                      required
                      maxLength={100}
                      pattern="[A-Za-z0-9_-]+"
                      value={form[key as keyof typeof form]}
                      onChange={(e) =>
                        change(key as keyof typeof form, e.target.value)
                      }
                    />
                  </Field>
                ))}
                <Field label="Amount">
                  <input
                    required
                    inputMode="decimal"
                    value={form.amount}
                    placeholder="125.00"
                    onChange={(e) => change("amount", e.target.value)}
                  />
                </Field>
                <Field label="Currency">
                  <select
                    value={form.currency}
                    onChange={(e) => change("currency", e.target.value)}
                  >
                    {[
                      "SAR",
                      "USD",
                      "EUR",
                      "GBP",
                      "AED",
                      "KWD",
                      "BHD",
                      "QAR",
                      "OMR",
                    ].map((c) => (
                      <option key={c}>{c}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Channel">
                  <select
                    value={form.channel}
                    onChange={(e) => change("channel", e.target.value)}
                  >
                    {["TRANSFER", "CARD", "ATM", "ONLINE"].map((c) => (
                      <option key={c}>{c}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Occurred at (your local time)">
                  <input
                    required
                    type="datetime-local"
                    value={form.occurred_at}
                    onChange={(e) => change("occurred_at", e.target.value)}
                  />
                </Field>
              </div>
              <details>
                <summary>Optional transaction context</summary>
                {[
                  ["card_id", "Synthetic card ID"],
                  ["device_id", "Device ID"],
                  ["destination_account_id", "Destination account ID"],
                ].map(([key, caption]) => (
                  <Field key={key} label={caption}>
                    <input
                      pattern="[A-Za-z0-9_-]+"
                      maxLength={100}
                      value={form[key as keyof typeof form]}
                      onChange={(e) =>
                        change(key as keyof typeof form, e.target.value)
                      }
                    />
                  </Field>
                ))}
              </details>
              <button type="submit">Submit transaction</button>
              <p className="muted">
                Unchanged retries reuse the same request key to prevent
                duplicates.
              </p>
            </fieldset>
          </form>
        </Panel>
        <div>
          <Panel title="Processing status">
            <div className="panel-body">
              <ErrorBox error={processing.error} />
              {selected ? (
                processing.data ? (
                  <>
                    <Badge value={processing.data.status} />
                    <p>
                      {processing.data.scoring_configured
                        ? "Processing is configured."
                        : "Scoring is unavailable; failed scoring will create a manual-review case."}
                    </p>
                    {processing.data.case_id && (
                      <p>
                        Review case: <code>{processing.data.case_id}</code>
                      </p>
                    )}
                    <Details value={processing.data.job} />
                  </>
                ) : (
                  <Loading />
                )
              ) : (
                <Empty title="Select or submit a transaction">
                  Processing status updates every ten seconds.
                </Empty>
              )}
            </div>
          </Panel>
          <Panel title="Batch import">
            <form
              className="panel-body"
              onSubmit={(e) => {
                e.preventDefault();
                action.run(async () => {
                  const payload = JSON.parse(batch);
                  if (
                    !Array.isArray(payload.items) ||
                    payload.items.length < 1 ||
                    payload.items.length > 100
                  )
                    throw new Error(
                      "Provide an items array containing 1–100 transactions, each with a stable idempotency_key.",
                    );
                  await api("/transactions/batch", {
                    method: "POST",
                    body: payload,
                  });
                }, "Batch accepted atomically.");
              }}
            >
              <fieldset disabled={action.busy}>
                <Field label="Batch JSON">
                  <textarea
                    rows={4}
                    required
                    value={batch}
                    onChange={(e) => setBatch(e.target.value)}
                    placeholder={
                      '{"items": [{"idempotency_key": "batch-item-1", ...}]}'
                    }
                  />
                </Field>
                <p className="muted">
                  Use integer minor units and timezone-aware timestamps. Every
                  item needs a stable idempotency key. Maximum 100 items / 128
                  KiB.
                </p>
                <button className="secondary">Submit batch</button>
              </fieldset>
            </form>
          </Panel>
        </div>
      </div>
      <Panel title="Submitted transactions">
        <ErrorBox error={list.error} />
        {list.loading ? (
          <Loading />
        ) : list.data?.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Transaction</th>
                  <th>Account</th>
                  <th>Amount</th>
                  <th>Occurred</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((t) => (
                  <tr key={t.id}>
                    <td>
                      <button
                        className="record-link"
                        onClick={() => setSelected(t.id)}
                      >
                        {t.external_id}
                      </button>
                    </td>
                    <td>{t.account_id}</td>
                    <td>{money(t.amount_minor, t.currency)}</td>
                    <td>{date(t.occurred_at)}</td>
                    <td>
                      <Badge value={t.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="No transactions yet" />
        )}
        <div className="table-footer">
          <button className="text-button" onClick={() => setCursor("")}>
            First page
          </button>
          {list.data?.next_cursor && (
            <button
              className="secondary"
              onClick={() => setCursor(String(list.data!.next_cursor))}
            >
              Next page
            </button>
          )}
        </div>
      </Panel>
    </>
  );
}
