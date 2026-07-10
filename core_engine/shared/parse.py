"""Parse Theta Terminal v2/v3 HTTP responses into DataFrames."""
from __future__ import annotations

import json
from typing import Any

import pandas as pd


def parse_response_body(body: str, status: int) -> Any:
    if status != 200 or not body or not body.strip():
        return None
    text = body.strip()
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}
    # NDJSON / CSV fallback
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    if lines[0].startswith("{"):
        return [json.loads(ln) for ln in lines]
    # CSV with header
    import io

    return pd.read_csv(io.StringIO(text))


def to_dataframe(payload: Any) -> pd.DataFrame:
    if payload is None:
        return pd.DataFrame()
    if isinstance(payload, pd.DataFrame):
        return payload
    if isinstance(payload, list):
        return pd.DataFrame(payload) if payload else pd.DataFrame()
    if isinstance(payload, dict):
        if "response" in payload and "format" in payload:
            cols = payload.get("format") or payload.get("header") or []
            rows = payload.get("response") or []
            if cols and rows:
                return pd.DataFrame(rows, columns=cols)
        if "data" in payload and isinstance(payload["data"], list):
            return pd.DataFrame(payload["data"])
        if "raw" in payload:
            return pd.DataFrame()
    return pd.DataFrame()


def normalize_chain_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    rename = {
        "implied_vol": "iv_api",
        "implied_volatility": "iv_api",
        "open_interest": "open_interest",
        "oi": "open_interest",
        "last": "last_trade_price",
        "size": "last_trade_size",
        "condition": "quote_condition",
        "quote_condition": "quote_condition",
    }
    for k, v in rename.items():
        if k in out.columns and v not in out.columns:
            out = out.rename(columns={k: v})
    if "expiration" in out.columns and "expiration_date" not in out.columns:
        out["expiration_date"] = out["expiration"]
    if "right" in out.columns and "option_type" not in out.columns:
        from .constants import normalize_right

        out["option_type"] = out["right"].map(normalize_right)
    for greek in ("delta", "gamma", "theta", "vega", "rho"):
        if greek in out.columns:
            out[f"{greek}_api"] = out[greek]
    if "iv_api" not in out.columns and "implied_vol" in out.columns:
        out["iv_api"] = out["implied_vol"]
    bid = pd.to_numeric(out.get("bid"), errors="coerce")
    ask = pd.to_numeric(out.get("ask"), errors="coerce")
    if "mid_price" not in out.columns:
        out["mid_price"] = (bid + ask) / 2.0
    return out