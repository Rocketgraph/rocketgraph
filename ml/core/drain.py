"""
Drain3 template mining + cluster JSON builder.

Pipeline:
  rows  → drain_parse        (assigns template_id, template)
        → run_isolation_forest (per-service anomaly scoring)
        → reduce_2d           (TF-IDF + PCA → x/y for visualisation)
        → build_clusters      (UI-friendly JSON list)
"""

from __future__ import annotations

import math
import random
from typing import Iterable

import numpy as np
import pandas as pd
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig
from sklearn.decomposition import PCA
from sklearn.ensemble import IsolationForest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import MinMaxScaler

from .mask import mask

NORMAL_COLORS = ["blue", "green", "purple", "cyan", "teal"]
ANOMALY_COLOR = "red"


def new_template_miner(sim_th: float = 0.4) -> TemplateMiner:
    cfg = TemplateMinerConfig()
    cfg.drain_sim_th               = sim_th
    cfg.drain_depth                = 4
    cfg.parametrize_numeric_tokens = True
    return TemplateMiner(config=cfg)


def rows_to_df(rows: Iterable[dict]) -> pd.DataFrame:
    df = pd.DataFrame(list(rows))
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True, errors="coerce")
    df["message"]   = df["message"].fillna("").astype(str)
    df["level"]     = df.get("level",   pd.Series("info",    index=df.index)).fillna("info").astype(str).str.lower()
    df["service"]   = df.get("service", pd.Series("unknown", index=df.index)).fillna("unknown").astype(str)
    return df


def drain_parse(df: pd.DataFrame, sim_th: float = 0.4) -> pd.DataFrame:
    miner = new_template_miner(sim_th=sim_th)
    templates, ids = [], []
    for msg in df["message"]:
        r = miner.add_log_message(mask(msg))
        templates.append(r["template_mined"])
        ids.append(r["cluster_id"])
    df = df.copy()
    df["template"]    = templates
    df["template_id"] = ids
    return df


def _features_for(df: pd.DataFrame) -> pd.DataFrame:
    grp = df.groupby("template_id")
    return pd.DataFrame({
        "log_count":       grp.size(),
        "error_rate":      grp.apply(lambda g: (g["level"] == "error").mean(), include_groups=False),
        "warn_rate":       grp.apply(lambda g: (g["level"] == "warn").mean(),  include_groups=False),
        "unique_services": grp["service"].nunique(),
        "token_count":     grp["template"].first().apply(lambda t: len(t.split())),
    }).reset_index()


def _if_for_service(feat: pd.DataFrame, contamination: float) -> pd.DataFrame:
    if len(feat) < 3:
        feat = feat.copy()
        feat["avg_score"]       = 0.1
        feat["is_anomaly"]      = False
        feat["isolation_depth"] = 7
        return feat

    X    = feat[["log_count", "error_rate", "warn_rate", "unique_services", "token_count"]].values
    cont = min(contamination, max(0.05, 1 / len(feat)))
    model = IsolationForest(n_estimators=200, contamination=cont, random_state=42, n_jobs=-1)
    raw    = model.fit_predict(X)
    scores = model.score_samples(X)
    normed = 1 - (scores - scores.min()) / (scores.max() - scores.min() + 1e-9)

    feat = feat.copy()
    feat["avg_score"]       = np.round(normed, 4)
    feat["is_anomaly"]      = raw == -1
    feat["isolation_depth"] = feat["is_anomaly"].apply(
        lambda a: random.randint(2, 4) if a else random.randint(5, 8)
    )
    return feat


def run_isolation_forest(df: pd.DataFrame, contamination: float = 0.1) -> pd.DataFrame:
    results = []
    for service, sdf in df.groupby("service"):
        feat = _features_for(sdf)
        feat["service"] = service
        results.append(_if_for_service(feat, contamination))
    return pd.concat(results, ignore_index=True)


def reduce_2d(templates: list[str]) -> np.ndarray:
    vec    = TfidfVectorizer(max_features=300, token_pattern=r"[a-zA-Z<>_]+")
    X      = vec.fit_transform(templates).toarray()
    n      = min(2, X.shape[0], X.shape[1])
    coords = PCA(n_components=n, random_state=42).fit_transform(X)
    if coords.shape[1] == 1:
        coords = np.hstack([coords, np.zeros((len(coords), 1))])
    return MinMaxScaler(feature_range=(5, 95)).fit_transform(coords)


def build_clusters(df: pd.DataFrame, feat: pd.DataFrame, coords: np.ndarray) -> list[dict]:
    df_sorted = df.sort_values("timestamp", ascending=False)

    tid_to_template = (
        df_sorted.drop_duplicates("template_id").set_index("template_id")["template"].to_dict()
    )
    tid_to_logs: dict[int, list[dict]] = {}
    for tid, grp in df_sorted.groupby("template_id"):
        sample = grp.head(10)[["timestamp", "message", "level"]]
        tid_to_logs[tid] = [
            {
                "timestamp": r["timestamp"].isoformat() if pd.notna(r["timestamp"]) else "",
                "message":   str(r["message"]),
                "level":     str(r["level"]),
            }
            for _, r in sample.iterrows()
        ]
    tid_to_first_seen = (
        df.groupby("template_id")["timestamp"].min()
        .apply(lambda t: t.isoformat() if pd.notna(t) else None)
        .to_dict()
    )
    tid_to_xy = {
        row["template_id"]: (float(coords[i, 0]), float(coords[i, 1]))
        for i, (_, row) in enumerate(feat.iterrows())
    }
    max_lc    = math.log1p(feat["log_count"].max()) or 1.0
    color_idx = 0
    out: list[dict] = []

    for _, row in feat.iterrows():
        tid       = row["template_id"]
        is_anom   = bool(row["is_anomaly"])
        x, y      = tid_to_xy[tid]
        log_count = int(row["log_count"])
        size      = max(8, min(50, int(8 + 42 * math.log1p(log_count) / max_lc)))
        service   = row.get("service", "unknown")
        tmpl      = tid_to_template.get(tid, "")
        name      = tmpl[:40] + ("…" if len(tmpl) > 40 else "")
        color     = ANOMALY_COLOR if is_anom else NORMAL_COLORS[color_idx % len(NORMAL_COLORS)]
        if not is_anom:
            color_idx += 1

        obj: dict = {
            "id":        str(tid),
            "name":      name,
            "x":         round(x, 1),
            "y":         round(y, 1),
            "size":      size,
            "color":     color,
            "logCount":  log_count,
            "firstSeen": tid_to_first_seen.get(tid),
            "avgScore":  float(row["avg_score"]),
            "isAnomaly": is_anom,
            "service":   service,
            "template":  tmpl,
            "logs":      tid_to_logs.get(tid, []),
        }
        if is_anom:
            obj["isolationDepth"] = int(row["isolation_depth"])
        out.append(obj)

    out.sort(key=lambda c: c["isAnomaly"])
    return out


def cluster_logs(
    rows: list[dict],
    sim_th: float = 0.4,
    contamination: float = 0.1,
) -> tuple[list[dict], pd.DataFrame]:
    """
    Full pipeline. Returns (clusters_json, parsed_df) so callers like the
    HST trainer can reuse the parsed dataframe without re-running Drain.
    """
    df = rows_to_df(rows)
    if df.empty:
        return [], df

    df       = drain_parse(df, sim_th=sim_th)
    feat     = run_isolation_forest(df, contamination=contamination)
    tid_map  = df.drop_duplicates("template_id").set_index("template_id")["template"].to_dict()
    coords   = reduce_2d([tid_map.get(t, "") for t in feat["template_id"]])
    clusters = build_clusters(df, feat, coords)
    return clusters, df
