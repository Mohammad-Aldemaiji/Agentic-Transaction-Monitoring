# E05 supervised scoring study — 27 October 2026

The existing XGBoost weights remain unchanged. This is an experimental threshold comparison, not a replacement model, calibration, model approval, or evidence of perfect performance.

## Result

On 2,899 eligible later-period payments, including 135 labelled fraud payments:

| Measure | Current threshold | Experimental threshold |
| --- | ---: | ---: |
| Threshold | 0.0102290465 | 0.0091383997 |
| Fraud caught | 87 | 91 |
| Fraud missed | 48 | 44 |
| False alarms | 10 | 11 |
| Recall | 64.44% | 67.41% |
| False-positive rate | 0.362% | 0.398% |

The candidate catches four additional fraud payments at the cost of one extra false alarm. It still misses 44 of 135 fraud payments and does **not** meet the project's 85% recall target. The live model's threshold has not been changed.

## Method and limits

- Use the saved model and its exact inference feature preparation. No label or investigation columns are model inputs.
- Choose the candidate threshold on 17 July–10 August only. The predeclared selection objective is at least 85% recall under 2% false positives, then highest sample precision; if infeasible, highest recall under that false-positive limit.
- Compare the fixed candidate and incumbent on 12 August–30 September. Boundary dates were excluded because the original notebook records dates rather than exact split timestamps. The original training dataset is unavailable, so temporal independence cannot be proven from row identifiers.
- Include only APPROVED SAR payments with supported application channel mappings. Of 6,676 sample rows in the final period, 2,899 are eligible. Unsupported/declined payments are not silently counted as correctly classified.
- The sample contains 1,305 fraud labels, the same count reported for the original 746,698-row dataset, but only 35,828 rows total. Normal sampling probabilities are unknown. Precision, aggregate accuracy, prevalence, alert volume and these results generally must not be represented as population performance.
- The test has now been inspected. Further feature/threshold tuning must use a new independent test set rather than repeatedly optimizing this one.

The demo uses four existing synthetic customers with 552, 523, 37 and 25 recorded payments. Two illustrate ordinary histories and two include labelled fraud in the evaluated period. They are intentionally selected examples, not evaluation evidence or real login identities. Only eligible later-period payments have scores; other history is explicitly unscored. Histories are incomplete samples of the original dataset. Ground-truth labels appear only in this offline demonstration and are never inserted into investigator evidence.

## Reproduce and inspect

From the project root:

```sh
backend/.venv/bin/python backend/scripts/evaluate_e05.py
```

This produces `.runtime/e05-evaluation.json`, including customer histories. The native local profile serves it through the authenticated sample-demo endpoint only when the recorded model and dataset digests match its configuration. Open **Explore supervised scoring and customer histories** above the review queue. This is a read-only research demonstration; it does not submit payments or activate the scoring worker.

`E05_SAMPLE_RESULTS.json` records the metrics, artifact digests and customer summary. Model registration/approval and worker activation remain separate operational steps. Do not register sample precision as production accuracy or promote this candidate as meeting the detection target.

Next improvement work requires new representative data or a separately documented synthetic generator, with realistic histories, labels available at training time, customer/time-separated validation, and a fresh test period. Prioritize the missed fraud patterns reported in the JSON before claiming a stronger trained model.
