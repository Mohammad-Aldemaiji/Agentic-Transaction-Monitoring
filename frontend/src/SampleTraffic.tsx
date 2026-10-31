import { lazy, Suspense, useState } from "react";
import { api } from "./api";
import { Loading, Panel, useAction } from "./ui";

const ScoringDemo = lazy(() => import("./ScoringDemo"));

export default function SampleTraffic() {
  const [showScoring, setShowScoring] = useState(false);
  const [offset, setOffset] = useState(0);
  const action = useAction();
  return (
    <Panel title="Sample traffic">
      <div className="panel-body">
        <p>
          Send ten historical sample payments for review. No real payments are
          made. Once processing finishes, refresh your review queue to see any
          new cases.
        </p>
        {action.feedback}
        <button
          disabled={action.busy || offset >= 50}
          onClick={() =>
            action.run(async () => {
              await api("/sample-traffic", {
                method: "POST",
                body: { offset },
              });
              setOffset((value) => value + 10);
            }, "Ten samples accepted. Previously submitted samples are reused without duplicates.")
          }
        >
          Simulate incoming traffic
        </button>
        <button
          className="secondary"
          onClick={() => setShowScoring((show) => !show)}
        >
          {showScoring
            ? "Hide scoring demo"
            : "Explore supervised scoring and customer histories"}
        </button>
        {showScoring && (
          <Suspense fallback={<Loading />}>
            <ScoringDemo />
          </Suspense>
        )}
        <p className="muted">
          {offset} / 50 samples submitted this visit. Automated scoring requires
          an active worker and approved model.
        </p>
      </div>
    </Panel>
  );
}
