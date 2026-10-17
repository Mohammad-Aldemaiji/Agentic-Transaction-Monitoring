"""Run the agent on one alert in the terminal, without the dashboard. Useful for
testing prompts and for evaluating the agent against the answer key.

    python -m src.agent.cli pick            # test-period alerts to try, with the answer key
    python -m src.agent.cli TX000123456     # score, analyse, and (if approved) verify

You play the analyst (approving) and the customer (answering the call).
"""
import json
import sys

from src.agent import investigator
from src.features.history import History
from src.models.fraud_model import FraudModel


class TerminalPhone:
    def ask(self, text):
        print(f"\n[CALL] agent: {text}")
        reply = input("  customer (empty = hang up)> ").strip()
        return reply or None

    def hang_up(self):
        print("[CALL ENDED]")


def show_tool(name, args, result):
    print(f"  -> {name}({json.dumps(args, ensure_ascii=False)})")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    h = History()
    if sys.argv[1] == "pick":
        for kind, ids in h.examples().items():
            print(f"{kind:28s} {ids[0]}")
        return
    tid = sys.argv[1]
    model = FraudModel(text_fields=h.df.drop(columns=["ts"]).select_dtypes(exclude="number").columns)
    result = model.score(h.record(tid))
    print(f"Model: p={result['probability']:.4f} risk={result['risk_score']} band={result['band']}")
    for r in result["reasons"]:
        print(f"   {r['label']:35s} {str(r['value']):>12s}  {r['direction']}")
    a = investigator.analyse(h, tid, result, on_tool=show_tool)
    print(json.dumps({k: v for k, v in a.items() if not k.startswith("_")}, indent=2, ensure_ascii=False))
    action = a["recommended_action"]
    if input(f"\nApprove {action}? (y/n)> ").strip().lower() == "y" and action == "VERIFY_WITH_CUSTOMER":
        r = h.row(tid)
        case = {"amount_sar": float(r["amount_sar"]), "time": str(r["timestamp"]), "channel": r["channel"],
                "merchant_or_beneficiary": r["merchant_name"] if isinstance(r["merchant_name"], str)
                else r["counterparty_id"], "analysis": a["summary"]}
        v = investigator.verify(case, TerminalPhone(), on_tool=show_tool)
        print(json.dumps({k: v[k] for k in ("customer_confirms", "signs_of_manipulation",
                                            "outcome", "outcome_note", "summary")}, indent=2))
    print("\nAnswer key (hidden from the agent):", h.truth(tid))


if __name__ == "__main__":
    main()
