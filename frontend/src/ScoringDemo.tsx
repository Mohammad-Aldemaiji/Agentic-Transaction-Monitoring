import { useState } from "react";
import { date } from "./api";
import { ErrorBox, Field, Loading, useResource } from "./ui";

type Metrics = {
  transactions: number;
  fraud: number;
  caught: number;
  missed: number;
  false_alarms: number;
  recall: number;
  false_positive_rate: number;
  threshold: number;
};
type Entry = {
  id: string;
  timestamp: string;
  amount: number;
  currency: string;
  channel: string;
  authorization: string;
  probability: number | null;
  fraud_label: boolean;
  baseline_alert: boolean | null;
  candidate_alert: boolean | null;
};
type Report = {
  scope: string;
  test_period: string;
  validation_period: string;
  test: { baseline: Metrics; candidate: Metrics };
  limitations: string[];
  customers: {
    name: string;
    account_id: string;
    scenario: string;
    transactions: number;
    fraud: number;
    evaluated: number;
    history: Entry[];
  }[];
};
const percent = (value: number) => (value * 100).toFixed(2) + "%";

export default function ScoringDemo() {
  const report = useResource<Report>("/sample-traffic/evaluation", 0);
  const [selected, setSelected] = useState(0),
    [page, setPage] = useState(0);
  const [evaluatedOnly, setEvaluatedOnly] = useState(false);
  if (report.error) return <ErrorBox error={report.error} />;
  if (!report.data) return <Loading />;
  const data = report.data,
    customer = data.customers[selected];
  const rows = customer.history.filter(
    (row) => !evaluatedOnly || row.probability !== null,
  );
  return (
    <section aria-label="Supervised scoring demonstration">
      <h3>Supervised model: measured results</h3>
      <p>{data.scope}</p>
      <p>
        Existing trained weights; experimental threshold tuning only. This does
        not activate automated decisions. Threshold chosen on{" "}
        {data.validation_period}. Final comparison: {data.test_period}.
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Final sample test</th>
              <th>Current threshold</th>
              <th>Experimental threshold</th>
            </tr>
          </thead>
          <tbody>
            {(
              [
                ["Fraud detected", "caught"],
                ["Fraud missed", "missed"],
                ["False alarms", "false_alarms"],
              ] as const
            ).map(([name, key]) => (
              <tr key={key}>
                <td>{name}</td>
                <td>{data.test.baseline[key]}</td>
                <td>{data.test.candidate[key]}</td>
              </tr>
            ))}
            <tr>
              <td>Fraud detection rate</td>
              <td>{percent(data.test.baseline.recall)}</td>
              <td>{percent(data.test.candidate.recall)}</td>
            </tr>
            <tr>
              <td>False-positive rate</td>
              <td>{percent(data.test.baseline.false_positive_rate)}</td>
              <td>{percent(data.test.candidate.false_positive_rate)}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p>
        {data.test.baseline.transactions.toLocaleString()} eligible test
        payments; {data.test.baseline.fraud} labelled fraud. Project target: at
        least 85% detection with no more than 2% false positives.
        {data.test.candidate.recall < 0.85
          ? " The experimental setting does not yet meet the detection target."
          : " These sample results still need independent validation."}
      </p>
      <details>
        <summary>What these results can and cannot show</summary>
        <ul>
          {data.limitations.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      </details>
      <h3>Sample customer histories</h3>
      <p>
        Illustrative synthetic customers, not login accounts. Historical
        activity is shown for context; only eligible final-period payments are
        evaluated. Known labels are shown for demonstration and never sent to
        the AI investigator.
      </p>
      <Field label="Sample customer">
        <select
          value={selected}
          onChange={(event) => {
            setSelected(Number(event.target.value));
            setPage(0);
          }}
        >
          {data.customers.map((c, i) => (
            <option key={c.account_id} value={i}>
              {c.name} · {c.transactions} transactions · {c.scenario}
            </option>
          ))}
        </select>
      </Field>
      <p>
        {customer.account_id} · {customer.transactions} recorded transactions ·{" "}
        {customer.evaluated} evaluated · {customer.fraud} known fraud labels
        across the history.
      </p>
      <label>
        <input
          type="checkbox"
          checked={evaluatedOnly}
          onChange={(event) => {
            setEvaluatedOnly(event.target.checked);
            setPage(0);
          }}
        />{" "}
        Show evaluated payments only
      </label>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Payment / date</th>
              <th>Amount / channel</th>
              <th>Known sample label</th>
              <th>Model output</th>
              <th>Current / experimental alert</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(page * 25, (page + 1) * 25).map((row) => (
              <tr key={row.id}>
                <td>
                  {row.id}
                  <br />
                  {date(row.timestamp)}
                </td>
                <td>
                  {row.amount.toLocaleString()} {row.currency}
                  <br />
                  {row.channel} · {row.authorization}
                </td>
                <td>
                  {row.fraud_label ? "Labelled fraud" : "Labelled normal"}
                </td>
                <td>
                  {row.probability === null
                    ? "Not evaluated"
                    : percent(row.probability)}
                </td>
                <td>
                  {row.probability === null
                    ? "—"
                    : `${row.baseline_alert ? "Alert" : "No alert"} / ${row.candidate_alert ? "Alert" : "No alert"}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        Page {page + 1} of {Math.max(1, Math.ceil(rows.length / 25))}. Model
        outputs are uncalibrated scores, not guarantees.
      </p>
      <button
        className="secondary"
        disabled={page === 0}
        onClick={() => setPage(page - 1)}
      >
        Previous history
      </button>
      <button
        className="secondary"
        disabled={(page + 1) * 25 >= rows.length}
        onClick={() => setPage(page + 1)}
      >
        Next history
      </button>
    </section>
  );
}
