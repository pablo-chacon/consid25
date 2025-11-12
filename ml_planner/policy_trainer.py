import os, json, time, math, pathlib, warnings
from typing import List, Tuple

warnings.filterwarnings("ignore", category=UserWarning)

try:
    import numpy as np
    import pandas as pd
    import xgboost as xgb
except Exception:
    np = pd = xgb = None  # graceful no-op if libs missing

RANDOM_SEED = 1337
REQUIRED_COLS = [
    "tick", "persona", "is_green", "station_speed_kw",
    "queue_len", "soc", "dist_to_goal_km",
    "kwh_gain", "cust_gain", "wait_ticks",
]


def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v is None or str(v).strip() == "" else v


def _to_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except:
        return default


def _to_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except:
        return default


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except:
        return 0.0


def _load_dataframe(csv_path: str):
    if pd is None: return None
    if not os.path.exists(csv_path): return None
    df = pd.read_csv(csv_path)
    df.columns = [c.lower() for c in df.columns]
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:  # schema not ready -> skip
        return None
    return df.dropna(subset=REQUIRED_COLS).copy()


def _feature_engineer(df):
    # Build target: alpha*kwh + beta*cust - gamma*wait
    alpha = _to_float("POLICY_ALPHA", 1.0)
    beta = _to_float("POLICY_BETA", 0.6)
    gamma = _to_float("POLICY_GAMMA", 0.2)
    y = (alpha * df["kwh_gain"] + beta * df["cust_gain"] - gamma * df["wait_ticks"]).astype(float)

    # Features
    import numpy as np  # safe; already imported
    out = df.copy()
    t = (out["tick"].astype(int) % 288).astype(float)
    out["tod_sin"] = np.sin(2 * math.pi * t / 288.0)
    out["tod_cos"] = np.cos(2 * math.pi * t / 288.0)
    out["persona"] = out["persona"].str.lower().fillna("neutral")
    dummies = pd.get_dummies(out["persona"], prefix="persona")
    out = pd.concat([out, dummies], axis=1)

    out["soc"] = out["soc"].clip(0.0, 1.0)
    out["queue_len"] = out["queue_len"].clip(0, 50)
    out["station_speed_kw"] = out["station_speed_kw"].clip(0, None)
    out["dist_to_goal_km"] = out["dist_to_goal_km"].clip(0, None)
    out["is_green"] = out["is_green"].astype(int).clip(0, 1)

    feat_cols = [
                    "is_green", "station_speed_kw", "queue_len", "soc", "dist_to_goal_km",
                    "tod_sin", "tod_cos",
                ] + sorted([c for c in out.columns if c.startswith("persona_")])

    X = out[feat_cols].astype(float)
    return X, y, feat_cols


def _should_retrain(data_path: str, model_path: str, force: bool) -> bool:
    if force: return True
    if not os.path.exists(model_path): return True
    return _mtime(data_path) > _mtime(model_path)


def auto_train() -> bool:
    """
    Auto-train if enabled & data present. Returns True if model written/updated.
    Env (all optional):
      POLICY_AUTOTRAIN=true|false (default true)
      POLICY_DATA_CSV=./data/charging_events.csv
      POLICY_OUTDIR=./model_artifacts
      POLICY_FORCE_RETRAIN=false
      POLICY_N_EST=350, POLICY_MAX_DEPTH=6, POLICY_LR=0.05
      POLICY_ALPHA=1.0, POLICY_BETA=0.6, POLICY_GAMMA=0.2
    """
    if _env("POLICY_AUTOTRAIN", "true").lower() != "true": return False
    if any(x is None for x in (np, pd, xgb)):  # libs missing
        return False

    data_csv = _env("POLICY_DATA_CSV", "./data/charging_events.csv")
    outdir = _env("POLICY_OUTDIR", "./model_artifacts")
    force = _env("POLICY_FORCE_RETRAIN", "false").lower() == "true"

    df = _load_dataframe(data_csv)
    if df is None or len(df) == 0:
        return False

    os.makedirs(outdir, exist_ok=True)
    model_path = os.path.join(outdir, "policy_xgb.json")
    feats_path = os.path.join(outdir, "policy_features.json")

    if not _should_retrain(data_csv, model_path, force):
        return False

    X, y, feat_cols = _feature_engineer(df)

    # reproducible split
    rng = np.random.RandomState(RANDOM_SEED)
    m = len(X)
    idx = np.arange(m);
    rng.shuffle(idx)
    split = int(m * 0.8)
    tr_idx, te_idx = idx[:split], idx[split:]
    Xtr, Xte = X.iloc[tr_idx], X.iloc[te_idx]
    ytr, yte = y.iloc[tr_idx], y.iloc[te_idx]

    model = xgb.XGBRegressor(
        n_estimators=_to_int("POLICY_N_EST", 350),
        learning_rate=_to_float("POLICY_LR", 0.05),
        max_depth=_to_int("POLICY_MAX_DEPTH", 6),
        subsample=0.9, colsample_bytree=0.9,
        reg_lambda=1.2, random_state=RANDOM_SEED, n_jobs=0,
    )
    model.fit(Xtr, ytr)

    # Simple health metric (RMSE) — not printed; just sanity guard
    pred = model.predict(Xte)
    rmse = float(np.sqrt(np.mean((pred - yte.values) ** 2)))
    if not np.isfinite(rmse):  # defensive
        return False

    model.save_model(model_path)
    with open(feats_path, "w") as f:
        json.dump(feat_cols, f)
    return True
