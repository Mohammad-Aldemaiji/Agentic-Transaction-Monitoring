"""The transaction history the system looks things up in.

This stands in for the bank's core-banking database: the scoring service reads a
transaction's pre-computed behavioural features from it, and the agent reads an
account's past activity from it. Every query takes a cut-off time, so nothing after
the moment of an alert is ever visible (no looking into the future).

The behavioural features (velocity, deviation from baseline, ...) come pre-computed
in the dataset. A live feature store that computes them as transactions arrive is
the feature-pipeline work (src/features), and would replace this class's lookups.
"""
import json
import threading

import pandas as pd

from src import config

LABELS = ["is_fraud", "fraud_type", "is_suspicious_aml", "aml_typology", "case_id",
          "label_source", "label_available_at", "weight_fraud", "weight_aml"]


class History:
    def __init__(self, path=config.DATA_FILE):
        self.path = path
        cache = path.with_suffix(".parquet")
        if cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime:
            df = pd.read_parquet(cache)
        else:
            print(f"Reading {path.name} (first run only, then cached as parquet)...")
            df = pd.read_csv(path, low_memory=False)
            df.to_parquet(cache)
        df["ts"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Riyadh")
        df = df.sort_values("ts", kind="stable").reset_index(drop=True)
        self.df = df
        self.pos = pd.Series(range(len(df)), index=df["txn_id"].values)
        self._by_account = {k: v for k, v in df.groupby("account_id", sort=False).indices.items()}
        self._by_device = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------- rows
    def has(self, txn_id: str) -> bool:
        return txn_id in self.pos.index

    def row(self, txn_id: str) -> pd.Series:
        return self.df.iloc[self.pos[txn_id]]

    def record(self, txn_id: str, with_labels: bool = False) -> dict:
        """A transaction as the bank's systems would see it: no labels."""
        r = self.row(txn_id)
        drop = ["ts"] + ([] if with_labels else LABELS)
        return json.loads(r.drop(drop).to_json())

    def truth(self, txn_id: str) -> dict:
        r = self.row(txn_id)
        ft = r["fraud_type"]
        return {"is_fraud": int(r["is_fraud"]), "fraud_type": ft if isinstance(ft, str) else None}

    # ------------------------------------------------------------- history
    def account_rows(self, account_id: str, before, days: int = 90) -> pd.DataFrame:
        idx = self._by_account.get(account_id)
        if idx is None:
            return self.df.iloc[0:0]
        h = self.df.iloc[idx]
        before = pd.Timestamp(before)
        return h[(h.ts < before) & (h.ts >= before - pd.Timedelta(days=days))]

    def device_rows(self, device_id: str, until) -> pd.DataFrame:
        with self._lock:
            if self._by_device is None:  # built on first use only
                self._by_device = self.df.groupby("device_id", sort=False).indices
        idx = self._by_device.get(device_id)
        if idx is None:
            return self.df.iloc[0:0]
        d = self.df.iloc[idx]
        return d[d.ts <= pd.Timestamp(until)]

    def replay_ids(self, start: str = config.TEST_START) -> list:
        """Transaction ids from the start of the test period onwards, in time order."""
        t = pd.Timestamp(start, tz="Asia/Riyadh")
        return self.df.loc[self.df.ts >= t, "txn_id"].tolist()

    def examples(self, start: str = config.TEST_START) -> dict:
        """Test-period transactions to inject in a demo: one per fraud type plus
        legitimate look-alikes. Ground truth is used only to pick them."""
        df = self.df[self.df.ts >= pd.Timestamp(start, tz="Asia/Riyadh")]
        out = {}
        for ft, g in df[df.is_fraud == 1].groupby("fraud_type"):
            out[ft] = g.txn_id.tolist()
        foreign = df[(df.is_fraud == 0) & df.channel.isin(["VISA_MC_ECOM", "MADA_ECOM"])
                     & (df.merchant_country != "SA") & (df.ip_country != "SA")]
        new_benef = df[(df.is_fraud == 0) & (df.channel == "IPS_SARIE")
                       & (df.is_new_counterparty == 1) & (df.amount_sar >= 10000)]
        out["LEGIT_FOREIGN_ECOM"] = foreign.txn_id.tolist()
        out["LEGIT_NEW_BENEFICIARY"] = new_benef.txn_id.tolist()
        return {k: v for k, v in out.items() if v}
