import { useState } from "react";
import { api, date, short } from "./api";
import { download } from "./Governance";
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
import type { Identity, Page } from "./types";
interface Model {
  id: string;
  name: string;
  digest: string;
  state: string;
  simulation: boolean;
  metrics: { precision: number; recall: number };
}
interface Training {
  id: string;
  state: string;
  snapshot_hash: string;
  model_id: string | null;
  created_at: string;
}
export default function Models({ identity }: { identity: Identity }) {
  const [rev, setRev] = useState(0),
    [tab, setTab] = useState("Models"),
    [cursor, setCursor] = useState(""),
    [name, setName] = useState(""),
    [digest, setDigest] = useState(""),
    [precision, setPrecision] = useState(""),
    [recall, setRecall] = useState(""),
    [simulation, setSimulation] = useState(true);
  const refresh = () => setRev((x) => x + 1),
    action = useAction(refresh);
  const models = useResource<Page<Model> & { configured_scorer: string }>(
    "/models?limit=30" + (tab === "Models" && cursor ? "&after=" + cursor : ""),
    rev,
  );
  const training = useResource<Page<Training>>(
    tab === "Reviewed datasets"
      ? "/training-jobs?limit=30" + (cursor ? "&after=" + cursor : "")
      : null,
    rev,
  );
  const drift = useResource<
    Page<{
      id: string;
      model_id: string;
      statistic: number;
      sample_size: number;
      window_end: string;
    }>
  >(
    tab === "Drift reports"
      ? "/drift-reports?limit=30" + (cursor ? "&after=" + cursor : "")
      : null,
    rev,
  );
  const [modelId, setModelId] = useState(""),
    [statistic, setStatistic] = useState(""),
    [sampleSize, setSampleSize] = useState(""),
    [windowEnd, setWindowEnd] = useState("");
  const next =
    tab === "Models"
      ? models.data?.next_cursor
      : tab === "Reviewed datasets"
        ? training.data?.next_cursor
        : drift.data?.next_cursor;
  return (
    <>
      <Title
        eyebrow="GOVERNANCE / MODEL LIFECYCLE"
        title="Evidence before automation"
        actions={<Refresh onClick={refresh} />}
      >
        Register evaluation results and manage reviewed datasets. This workspace
        does not train a model.
      </Title>
      <div className="notice">
        Configured scoring:{" "}
        <strong>{models.data?.configured_scorer ?? "Loading…"}</strong>.
        Registered metrics are operator-supplied evidence, not independently
        validated results.
      </div>
      {action.feedback}
      <nav className="tabs">
        {["Models", "Reviewed datasets", "Drift reports"].map((t) => (
          <button
            className={tab === t ? "active" : ""}
            key={t}
            onClick={() => {
              setTab(t);
              setCursor("");
            }}
          >
            {t}
          </button>
        ))}
      </nav>
      {tab === "Models" && (
        <div className="case-columns">
          <Panel title="Model register">
            <div className="panel-body">
              <ErrorBox error={models.error} />
              {models.loading ? (
                <Loading />
              ) : models.data?.items.length ? (
                models.data.items.map((m) => (
                  <article className="proposal" key={m.id}>
                    <div className="panel-heading">
                      <strong>{m.name}</strong>
                      <Badge value={m.state} />
                    </div>
                    <p>
                      {m.simulation
                        ? "Simulation model"
                        : "External model artifact"}
                    </p>
                    <p>
                      Precision {m.metrics.precision} · Recall{" "}
                      {m.metrics.recall}
                    </p>
                    <small className="break">SHA-256: {m.digest}</small>
                    {identity.role === "supervisor" &&
                      m.state === "REGISTERED" && (
                        <div className="button-row">
                          <button
                            disabled={action.busy}
                            onClick={() =>
                              action.run(() =>
                                api("/models/" + m.id + "/review", {
                                  method: "POST",
                                  body: { expected_version: 1, approved: true },
                                }),
                              )
                            }
                          >
                            Approve model
                          </button>
                          <button
                            className="secondary"
                            disabled={action.busy}
                            onClick={() =>
                              action.run(() =>
                                api("/models/" + m.id + "/review", {
                                  method: "POST",
                                  body: {
                                    expected_version: 1,
                                    approved: false,
                                  },
                                }),
                              )
                            }
                          >
                            Reject model
                          </button>
                        </div>
                      )}
                  </article>
                ))
              ) : (
                <Empty title="No models registered" />
              )}
            </div>
          </Panel>
          {identity.role === "admin" && (
            <Panel title="Register evaluated model">
              <form
                className="panel-body"
                onSubmit={(e) => {
                  e.preventDefault();
                  action.run(() =>
                    api("/models", {
                      method: "POST",
                      body: {
                        name,
                        digest,
                        simulation,
                        precision: Number(precision),
                        recall: Number(recall),
                      },
                    }),
                  );
                }}
              >
                <fieldset disabled={action.busy}>
                  <Field label="Model name">
                    <input
                      required
                      pattern="[A-Za-z0-9_-]+"
                      maxLength={100}
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                    />
                  </Field>
                  <Field label="Artifact SHA-256">
                    <input
                      required
                      pattern="[a-f0-9]{64}"
                      value={digest}
                      onChange={(e) => setDigest(e.target.value)}
                    />
                  </Field>
                  <Field label="Measured precision">
                    <input
                      required
                      type="number"
                      min="0"
                      max="1"
                      step="any"
                      value={precision}
                      onChange={(e) => setPrecision(e.target.value)}
                    />
                  </Field>
                  <Field label="Measured recall">
                    <input
                      required
                      type="number"
                      min="0"
                      max="1"
                      step="any"
                      value={recall}
                      onChange={(e) => setRecall(e.target.value)}
                    />
                  </Field>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={simulation}
                      onChange={(e) => setSimulation(e.target.checked)}
                    />
                    Simulation artifact
                  </label>
                  <button>Register model</button>
                </fieldset>
              </form>
            </Panel>
          )}
        </div>
      )}
      {tab === "Reviewed datasets" && (
        <Panel
          title="Reviewed-label snapshots"
          aside={
            identity.role === "supervisor" && (
              <button
                disabled={action.busy}
                onClick={() =>
                  action.run(
                    () => api("/training-jobs", { method: "POST", body: {} }),
                    "Snapshot created from reviewed decisions.",
                  )
                }
              >
                Create snapshot
              </button>
            )
          }
        >
          <div className="panel-body">
            <ErrorBox error={training.error} />
            {training.loading ? (
              <Loading />
            ) : training.data?.items.length ? (
              training.data.items.map((t) => (
                <TrainingCard
                  key={t.id + "-" + rev}
                  training={t}
                  models={models.data?.items ?? []}
                  identity={identity}
                  refresh={refresh}
                />
              ))
            ) : (
              <Empty title="No reviewed datasets">
                A supervisor can create a snapshot once cases have legitimate or
                confirmed-fraud decisions.
              </Empty>
            )}
          </div>
        </Panel>
      )}
      {tab === "Drift reports" && (
        <div className="case-columns">
          <Panel title="Recorded drift">
            <div className="panel-body">
              <ErrorBox error={drift.error} />
              {drift.loading ? (
                <Loading />
              ) : drift.data?.items.length ? (
                drift.data.items.map((d) => (
                  <article key={d.id} className="note">
                    <strong>Model {short(d.model_id)}</strong>
                    <p>
                      Statistic: {d.statistic} · Samples: {d.sample_size}
                    </p>
                    <small>{date(d.window_end)}</small>
                  </article>
                ))
              ) : (
                <Empty title="No drift reports" />
              )}
            </div>
          </Panel>
          {identity.role === "admin" && (
            <Panel title="Record measured drift">
              <form
                className="panel-body"
                onSubmit={(e) => {
                  e.preventDefault();
                  action.run(() =>
                    api("/drift-reports", {
                      method: "POST",
                      body: {
                        model_id: modelId,
                        statistic: Number(statistic),
                        sample_size: Number(sampleSize),
                        window_end: new Date(windowEnd).toISOString(),
                      },
                    }),
                  );
                }}
              >
                <fieldset disabled={action.busy}>
                  <Field label="Model">
                    <select
                      required
                      value={modelId}
                      onChange={(e) => setModelId(e.target.value)}
                    >
                      <option value="">Select model</option>
                      {models.data?.items.map((m) => (
                        <option value={m.id} key={m.id}>
                          {m.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Measured statistic">
                    <input
                      required
                      type="number"
                      min="0"
                      step="any"
                      value={statistic}
                      onChange={(e) => setStatistic(e.target.value)}
                    />
                  </Field>
                  <Field label="Sample size">
                    <input
                      required
                      type="number"
                      min="100"
                      step="1"
                      value={sampleSize}
                      onChange={(e) => setSampleSize(e.target.value)}
                    />
                  </Field>
                  <Field label="Window end (local time)">
                    <input
                      required
                      type="datetime-local"
                      value={windowEnd}
                      onChange={(e) => setWindowEnd(e.target.value)}
                    />
                  </Field>
                  <button>Record drift report</button>
                </fieldset>
              </form>
            </Panel>
          )}
        </div>
      )}
      <div className="table-footer">
        {cursor && (
          <button className="text-button" onClick={() => setCursor("")}>
            First page
          </button>
        )}
        {next && (
          <button className="secondary" onClick={() => setCursor(String(next))}>
            Next page
          </button>
        )}
      </div>
    </>
  );
}
function TrainingCard({
  training: t,
  models,
  identity,
  refresh,
}: {
  training: Training;
  models: Model[];
  identity: Identity;
  refresh: () => void;
}) {
  const [reason, setReason] = useState(""),
    [model, setModel] = useState("");
  const action = useAction(refresh),
    status = useResource<{
      sample_size: number;
      cancellation_reason: string | null;
    }>("/training-jobs/" + t.id);
  return (
    <article className="proposal">
      <div className="panel-heading">
        <strong>Snapshot {short(t.id)}</strong>
        <Badge value={t.state} />
      </div>
      <ErrorBox error={status.error} />
      <p>
        {status.data?.sample_size ?? "—"} reviewed examples ·{" "}
        {date(t.created_at)}
      </p>
      <small className="break">Snapshot hash: {t.snapshot_hash}</small>
      {status.data?.cancellation_reason && (
        <p>{status.data.cancellation_reason}</p>
      )}
      {action.feedback}
      <fieldset disabled={action.busy}>
        {identity.role !== "auditor" && (
          <button
            className="secondary"
            onClick={() =>
              action.run(
                () =>
                  download(
                    "/training-jobs/" + t.id + "/dataset",
                    "reviewed-dataset-" + short(t.id) + ".json",
                  ),
                "Dataset downloaded.",
              )
            }
          >
            Download dataset
          </button>
        )}
        {t.state === "READY" && identity.role === "supervisor" && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              action.run(() =>
                api("/training-jobs/" + t.id + "/cancel", {
                  method: "POST",
                  body: { reason },
                }),
              );
            }}
          >
            <Field label="Cancellation reason">
              <input
                required
                minLength={5}
                maxLength={2000}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </Field>
            <button className="secondary danger">Cancel snapshot</button>
          </form>
        )}
        {t.state === "READY" && identity.role === "admin" && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              action.run(() =>
                api("/training-jobs/" + t.id + "/complete", {
                  method: "POST",
                  body: { model_id: model, snapshot_hash: t.snapshot_hash },
                }),
              );
            }}
          >
            <Field label="Resulting registered model">
              <select
                required
                value={model}
                onChange={(e) => setModel(e.target.value)}
              >
                <option value="">Select model</option>
                {models.map((m) => (
                  <option value={m.id} key={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </Field>
            <button>Link training result</button>
          </form>
        )}
      </fieldset>
      {t.model_id && <p>Resulting model: {short(t.model_id)}</p>}
    </article>
  );
}
