# Fraud model E05

XGBoost classifier that scores each transaction for fraud (`is_fraud`).
Trained by `notebooks/fraud_model_E05.ipynb` on the synthetic Saudi bank dataset.

| File | What it is |
|---|---|
| `fraud_model_e05.json` | The model in XGBoost's own format. Loads across XGBoost versions. **Use this one.** |
| `fraud_model_e05.pkl` | The same model pickled with joblib. Only loads reliably with xgboost 3.4.1. |
| `model_columns.pkl` | The 328 input columns, in the exact order the model expects. |
| `model_settings.pkl` | Alert budget (6 per day), score threshold, and which split it was evaluated on. |
| `requirements.txt` | Library versions. |

## Settings

- Model: `XGBClassifier(n_estimators=300, max_depth=6, learning_rate=0.1, subsample=0.8, colsample_bytree=0.8, tree_method="hist", random_state=42)`
- Features: all dataset features plus 8 engineered ones (see notebook step 5)
- Trained on: the first 70% of transactions by time (the validation run, `eval_set: val`)

## Loading it

```python
import joblib
import pandas as pd
from xgboost import XGBClassifier

model = XGBClassifier()
model.load_model("models/fraud_e05/fraud_model_e05.json")
columns = joblib.load("models/fraud_e05/model_columns.pkl")
settings = joblib.load("models/fraud_e05/model_settings.pkl")

# X must be built exactly as in the notebook (same 8 features, same one-hot encoding)
X = X.reindex(columns=columns, fill_value=0)
scores = model.predict_proba(X)[:, 1]
flagged = scores >= settings["threshold"]
```

`reindex` matters: if a column is missing or in a different order, the model
gives wrong scores without raising an error.

Only load `.pkl` files from inside the team. Loading a pickle can run code.
