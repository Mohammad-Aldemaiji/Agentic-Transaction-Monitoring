"""Fraud model E05: feature preparation, scoring and severity bands.

The preparation below must stay identical to notebooks/fraud_model_E05.ipynb
(steps 5 and 6). If the model is retrained with different features, change both.
"""
import math

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb

from src import config

# Notebook step 6: identifiers, free text and everything known only after an investigation.
DROP = [
    "txn_id", "timestamp", "booking_date", "account_id", "customer_id",
    "merchant_id", "terminal_id", "counterparty_id", "session_id", "device_id",
    "merchant_name", "narrative", "corridor", "merchant_city", "txn_city",
    "home_city", "counterparty_bank_code", "os",
    "is_fraud", "fraud_type", "is_suspicious_aml", "aml_typology", "case_id",
    "label_source", "label_available_at", "weight_fraud", "weight_aml",
]

# Severity bands on the fraud probability. The alert line is the model's own threshold
# (its 6-alerts-per-day budget on the validation period).
BANDS = [(0.50, "CRITICAL"), (0.10, "HIGH")]


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Notebook step 5: the 8 engineered features."""
    df = df.copy()
    df["new_device_new_payee"] = ((df["is_new_device"] == 1) & (df["is_new_counterparty"] == 1)).astype(int)
    df["new_device_x_amount"] = df["is_new_device"] * df["amount_over_income"]
    df["fast_after_login"] = ((df["seconds_since_login"] >= 0) & (df["seconds_since_login"] < 120)).astype(int)
    df["vpn_new_device"] = ((df["is_proxy_vpn"] == 1) & (df["is_new_device"] == 1)).astype(int)
    df["foreign_ip_no_3ds"] = ((df["ip_country"] != "SA") & (df["entry_mode"] == "ECOM_NO_3DS")).astype(int)
    df["share_of_day_spend"] = df["amount_sar"] / (df["amount_sum_24h"] + 1)
    df["unusual_night"] = ((df["hour"] <= 5) & (df["hour_deviation_score"] > 2)).astype(int)
    df["near_sarie_cap"] = ((df["channel"] == "IPS_SARIE") & (df["amount_sar"] >= 17000)
                            & (df["amount_sar"] < 20000)).astype(int)
    return df


class FraudModel:
    name = "fraud_e05"

    def __init__(self, model_dir=config.MODEL_DIR, text_fields=None):
        self.booster = xgb.Booster()
        self.booster.load_model(str(model_dir / "fraud_model_e05.json"))
        self.columns = list(joblib.load(model_dir / "model_columns.pkl"))
        settings = joblib.load(model_dir / "model_settings.pkl")
        self.threshold = float(settings["threshold"])
        self.alerts_per_day = settings.get("alerts_per_day")
        self._groups = self._build_groups(text_fields) if text_fields is not None else None

    # ------------------------------------------------------------- preparation
    def prepare(self, records) -> pd.DataFrame:
        """Raw transaction records (dicts or a DataFrame) -> the model's input matrix."""
        df = records.copy() if isinstance(records, pd.DataFrame) else pd.DataFrame(list(records))
        df = df.drop(columns=[c for c in DROP + ["ts"] if c in df.columns])
        for c in df.columns:  # incoming JSON may carry numbers as strings or nulls
            if c in self.columns and df[c].dtype == object:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = add_features(df)
        text = df.select_dtypes(exclude="number").columns
        X = pd.get_dummies(df, columns=text).fillna(0)
        return X.reindex(columns=self.columns, fill_value=0).astype("float32")

    # ------------------------------------------------------------- scoring
    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.booster.predict(xgb.DMatrix(X, feature_names=self.columns))

    def score(self, record: dict) -> dict:
        X = self.prepare([record])
        p = float(self.predict(X)[0])
        return {"probability": p, "risk_score": self.risk_score(p), "band": self.band(p),
                "is_alert": p >= self.threshold, "reasons": self.reasons(X, record)}

    def band(self, p: float) -> str:
        if p < self.threshold:
            return "LOW"
        for cut, name in BANDS:
            if p >= cut:
                return name
        return "MEDIUM"

    def risk_score(self, p: float) -> int:
        """A 0-100 display scale: 50 is the alert line, 100 is certainty, log-spaced in
        between. It is a ranking scale, not a calibrated probability; the probability is
        reported next to it. Fusion with the anomaly layer will replace it."""
        t, floor = self.threshold, 1e-5
        p = min(max(p, floor), 1.0)
        if p >= t:
            return round(50 + 50 * math.log(p / t) / math.log(1 / t))
        return round(50 * math.log(p / floor) / math.log(t / floor))

    # ------------------------------------------------------------- explanations
    def reasons(self, X: pd.DataFrame, record: dict, k: int = 5) -> list:
        """Top-k features behind this score: SHAP values from XGBoost's own tree
        explainer (pred_contribs), with one-hot columns folded back into the original
        field, e.g. channel_IPS_SARIE -> channel = IPS_SARIE."""
        contrib = self.booster.predict(xgb.DMatrix(X, feature_names=self.columns),
                                       pred_contribs=True)[0][:-1]  # last value is the bias
        groups = self._group_map(record)
        total = {}
        for col, v in zip(self.columns, contrib):
            if v:
                g = groups.get(col, col)
                total[g] = total.get(g, 0.0) + float(v)
        top = sorted(total.items(), key=lambda kv: -abs(kv[1]))[:k]
        out = []
        for feat, v in top:
            val = record.get(feat)
            out.append({"feature": feat, "label": LABELS.get(feat, feat.replace("_", " ").capitalize()),
                        "value": _fmt(val), "contribution": round(v, 3),
                        "direction": "raises risk" if v > 0 else "lowers risk"})
        return out

    def _group_map(self, record: dict) -> dict:
        if self._groups is None:
            self._groups = self._build_groups({k for k, v in record.items() if isinstance(v, str)})
        return self._groups

    def _build_groups(self, text_fields) -> dict:
        """model column -> the text field it was one-hot encoded from."""
        fields = sorted(set(text_fields) - set(DROP), key=len, reverse=True)
        g = {}
        for col in self.columns:
            for f in fields:
                if col.startswith(f + "_"):
                    g[col] = f
                    break
        return g


def _fmt(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if isinstance(v, float):
        return round(v, 3)
    return v


LABELS = {
    "amount_sar": "Amount (SAR)", "channel": "Payment channel", "entry_mode": "Entry mode",
    "merchant_category": "Merchant category", "counterparty_type": "Counterparty type",
    "counterparty_country": "Counterparty country", "ip_country": "IP country",
    "is_proxy_vpn": "Proxy / VPN", "is_3ds_authenticated": "3-D Secure",
    "seconds_since_login": "Seconds since login", "distance_from_home_km": "Distance from home (km)",
    "is_new_device": "New device", "is_new_counterparty": "New counterparty",
    "beneficiary_age_days": "Beneficiary age (days)", "is_new_country": "New country",
    "is_new_city": "New city", "is_new_mcc": "New merchant category",
    "txn_count_1h": "Transactions in last hour", "txn_count_24h": "Transactions in last 24 h",
    "declined_count_24h": "Declines in last 24 h", "amount_sum_24h": "Spent in last 24 h",
    "amount_over_p95_est": "Amount vs account's usual max", "amount_over_income": "Amount vs monthly income",
    "outflow_24h_over_income": "24 h outflow vs income", "amount_log_z_90d": "Amount z-score (90 d)",
    "hour": "Hour of day", "hour_deviation_score": "Unusual hour for this customer",
    "accounts_sharing_device": "Accounts on this device", "impossible_travel_kmh": "Implied travel speed (km/h)",
    "cnp_ratio_24h": "Card-not-present share (24 h)", "no_3ds_ratio_24h": "No-3DS share (24 h)",
    "small_then_large_flag": "Small tests then large payment", "mcc_risk_score": "Merchant category risk",
    "is_round_amount": "Round amount", "is_cross_border": "Cross-border",
    "distinct_beneficiaries_corridor_30d": "Beneficiaries in corridor (30 d)",
    "passthrough_ratio_24h": "Pass-through ratio (24 h)", "account_age_days": "Account age (days)",
    "status": "Authorisation status", "decline_reason": "Decline reason",
    "new_device_new_payee": "New device paying a new payee", "new_device_x_amount": "New device × amount",
    "fast_after_login": "Paid within 2 min of login", "vpn_new_device": "VPN on a new device",
    "foreign_ip_no_3ds": "Foreign IP without 3-D Secure", "share_of_day_spend": "Share of the day's spending",
    "unusual_night": "Unusual night activity", "near_sarie_cap": "Just under the sarie cap",
    "employment_sector": "Employment sector", "customer_type": "Customer type",
    "hijri_month": "Hijri month", "count_just_below_2500_24h": "sarie transfers just under SAR 2,500 (24 h)",
    "count_just_below_20k_7d": "Transfers just under SAR 20,000 (7 d)", "is_ramadan": "During Ramadan",
    "days_to_month_end": "Days to month end", "days_since_salary_credit": "Days since salary",
    "balance_before": "Balance before", "balance_after": "Balance after", "is_new_mcc": "New merchant category",
    "remit_over_income_ratio": "Remittance vs income", "is_first_time_corridor": "First transfer to this country",
    "seconds_inflow_to_outflow": "Seconds from money in to money out", "fan_out_count_7d": "Distinct payees (7 d)",
    "fan_in_count_7d": "Distinct payers (7 d)", "txn_count_7d": "Transactions in last 7 d",
    "distinct_counterparties_7d": "Distinct counterparties (7 d)", "distinct_devices_7d": "Devices used (7 d)",
    "residency_status": "Residency status", "currency": "Currency", "direction": "Direction",
}
