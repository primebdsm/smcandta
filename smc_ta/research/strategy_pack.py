"""Repeatable strategy research pack for SMC + TA hypotheses."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, replace
from html import escape
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from smc_ta.backtest import BacktestConfig, BacktestResult, run_backtest
from smc_ta.forex.sessions import session_labels
from smc_ta.monitoring.metrics import performance_summary
from smc_ta.risk import RiskConfig
from smc_ta.smc import classify_smc_setups
from smc_ta.strategy import get_strategy_profile
from smc_ta.validation import normalize_ohlcv
from smc_ta.walkforward import WalkForwardCandidate, WalkForwardConfig, run_walk_forward


@dataclass(frozen=True)
class StrategyResearchHypothesis:
    """One named SMC + TA research hypothesis.

    These are research descriptions, not market predictions. They describe what
    evidence the pack should collect from historical candles.
    """

    name: str
    profile_name: str
    thesis: str
    expected_setups: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ("EURUSD",)
    tags: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class StrategyResearchGrid:
    """Candidate expansion settings around a named strategy profile."""

    min_signal_score_offsets: tuple[int, ...] = (-1, 0, 1)
    adx_threshold_offsets: tuple[float, ...] = (-2.0, 0.0, 2.0)
    max_poi_atr_distance_multipliers: tuple[float, ...] = (0.75, 1.0, 1.25)
    max_spread_pips_multipliers: tuple[float, ...] = (0.8, 1.0, 1.2)
    risk_percent_multipliers: tuple[float, ...] = (0.5, 1.0)
    max_candidates_per_hypothesis: int = 18


@dataclass(frozen=True)
class StrategyResearchCandidate:
    """One generated backtest/walk-forward candidate."""

    name: str
    hypothesis_name: str
    profile_name: str
    symbol: str
    config: BacktestConfig
    parameters: Mapping[str, Any] = field(default_factory=dict)
    thesis: str = ""
    tags: tuple[str, ...] = ()

    def walk_forward_candidate(self) -> WalkForwardCandidate:
        """Return the candidate object used by the walk-forward optimizer."""

        return WalkForwardCandidate(self.name, self.config)


@dataclass(frozen=True)
class StrategyResearchConfig:
    """Settings for a full research-pack run."""

    hypotheses: tuple[StrategyResearchHypothesis, ...] = field(
        default_factory=lambda: DEFAULT_STRATEGY_RESEARCH_HYPOTHESES
    )
    grid: StrategyResearchGrid = field(default_factory=StrategyResearchGrid)
    symbols: tuple[str, ...] | None = None
    run_walk_forward: bool = False
    walk_forward: WalkForwardConfig | None = None
    max_walk_forward_candidates: int = 5
    min_trades: int = 3
    min_profit_factor: float = 1.05
    min_total_return_percent: float = 0.0
    max_drawdown_percent: float = 10.0
    min_win_rate_percent: float | None = None


@dataclass(frozen=True)
class StrategyResearchArtifacts:
    """Paths written by `write_strategy_research_pack`."""

    output_dir: Path
    summary_json: Path
    hypotheses_csv: Path
    candidates_csv: Path
    setup_report_csv: Path
    session_report_csv: Path
    walk_forward_summary_csv: Path
    walk_forward_rankings_csv: Path
    promotion_report_csv: Path
    html_report: Path


@dataclass(frozen=True)
class StrategyResearchResult:
    """Complete strategy research output."""

    config: StrategyResearchConfig
    hypotheses: pd.DataFrame
    candidates: pd.DataFrame
    setup_report: pd.DataFrame
    session_report: pd.DataFrame
    walk_forward_summary: pd.DataFrame
    walk_forward_rankings: pd.DataFrame
    promotion_report: pd.DataFrame
    artifacts: StrategyResearchArtifacts | None = None

    @property
    def ok(self) -> bool:
        return bool(
            not self.promotion_report.empty
            and self.promotion_report["research_status"].eq("demo_candidate").any()
        )

    def summary(self) -> str:
        if self.promotion_report.empty:
            return "strategy_research_no_candidates"
        counts = self.promotion_report["research_status"].value_counts().to_dict()
        return ";".join(f"{key}={int(value)}" for key, value in sorted(counts.items()))

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary(),
            "config": _jsonable(_config_dict(self.config)),
            "hypotheses": _records(self.hypotheses),
            "top_candidates": _records(_top_candidates(self.candidates, limit=10)),
            "promotion_report": _records(self.promotion_report),
            "walk_forward_summary": _records(self.walk_forward_summary),
            "artifacts": _bundle_dict(self.artifacts),
        }

    def with_artifacts(self, artifacts: StrategyResearchArtifacts) -> "StrategyResearchResult":
        return replace(self, artifacts=artifacts)


DEFAULT_STRATEGY_RESEARCH_HYPOTHESES: tuple[StrategyResearchHypothesis, ...] = (
    StrategyResearchHypothesis(
        name="london_liquidity_sweep_reversal",
        profile_name="london_killzone",
        thesis="Test whether London kill-zone liquidity sweeps with SMC/TA agreement produce cleaner M5 reversal evidence.",
        expected_setups=("liquidity_sweep_choch", "london_sweep_reversal", "premium_reversal"),
        symbols=("EURUSD", "GBPUSD"),
        tags=("smc", "liquidity", "session", "reversal"),
    ),
    StrategyResearchHypothesis(
        name="new_york_liquidity_sweep_reversal",
        profile_name="ny_session_reversal",
        thesis="Test whether New York sweep/reversal conditions behave differently from London conditions.",
        expected_setups=("liquidity_sweep_choch", "premium_reversal"),
        symbols=("EURUSD", "GBPUSD", "USDJPY"),
        tags=("smc", "liquidity", "session", "reversal"),
    ),
    StrategyResearchHypothesis(
        name="intraday_fvg_continuation",
        profile_name="intraday_m15",
        thesis="Test M15 continuation after fair value gap context agrees with trend and momentum.",
        expected_setups=("fvg_continuation", "discount_continuation"),
        symbols=("EURUSD", "GBPUSD", "USDJPY"),
        tags=("smc", "fvg", "trend", "momentum"),
    ),
    StrategyResearchHypothesis(
        name="intraday_order_block_mitigation",
        profile_name="intraday_m15",
        thesis="Test order-block mitigation entries only when technical trend and volatility context are aligned.",
        expected_setups=("order_block_mitigation",),
        symbols=("EURUSD", "GBPUSD", "AUDUSD"),
        tags=("smc", "order_block", "trend", "volatility"),
    ),
    StrategyResearchHypothesis(
        name="swing_premium_discount_rebalance",
        profile_name="swing_h4",
        thesis="Test H4 swing entries from premium/discount context with wider confirmation windows.",
        expected_setups=("premium_reversal", "discount_continuation", "order_block_mitigation"),
        symbols=("EURUSD", "GBPUSD", "USDJPY", "AUDUSD"),
        tags=("smc", "premium_discount", "swing"),
    ),
)


def default_strategy_research_hypotheses() -> tuple[StrategyResearchHypothesis, ...]:
    """Return the built-in Forex SMC + TA research hypotheses."""

    return DEFAULT_STRATEGY_RESEARCH_HYPOTHESES


def build_strategy_research_candidates(
    *,
    hypotheses: tuple[StrategyResearchHypothesis, ...] | None = None,
    grid: StrategyResearchGrid | None = None,
    symbols: tuple[str, ...] | None = None,
) -> tuple[StrategyResearchCandidate, ...]:
    """Build deterministic candidate configs from hypotheses and profile presets."""

    selected_hypotheses = hypotheses or DEFAULT_STRATEGY_RESEARCH_HYPOTHESES
    cfg = grid or StrategyResearchGrid()
    symbol_filter = {symbol.upper() for symbol in symbols} if symbols else None
    candidates: list[StrategyResearchCandidate] = []
    for hypothesis in selected_hypotheses:
        profile = get_strategy_profile(hypothesis.profile_name)
        hypothesis_symbols = tuple(symbol.upper() for symbol in hypothesis.symbols)
        active_symbols = tuple(symbol for symbol in hypothesis_symbols if symbol_filter is None or symbol in symbol_filter)
        for symbol in active_symbols:
            candidates.extend(_expand_hypothesis_candidates(hypothesis, symbol, profile.backtest, profile.risk, cfg))
    return tuple(candidates)


def run_strategy_research_pack(
    candles_by_symbol: Mapping[str, pd.DataFrame] | pd.DataFrame,
    *,
    config: StrategyResearchConfig | None = None,
) -> StrategyResearchResult:
    """Run backtest and optional walk-forward evidence for strategy candidates."""

    cfg = config or StrategyResearchConfig()
    candles = _candles_mapping(candles_by_symbol, cfg)
    hypotheses = _hypotheses_frame(cfg.hypotheses)
    candidate_defs = build_strategy_research_candidates(hypotheses=cfg.hypotheses, grid=cfg.grid, symbols=cfg.symbols)
    candidate_rows: list[dict[str, Any]] = []
    setup_frames: list[pd.DataFrame] = []
    session_frames: list[pd.DataFrame] = []

    for candidate in candidate_defs:
        data = candles.get(candidate.symbol)
        if data is None:
            candidate_rows.append(_missing_data_row(candidate))
            continue
        result = run_backtest(data, config=candidate.config)
        metrics = _candidate_metrics(candidate, result)
        candidate_rows.append(metrics)
        setup_frames.append(_setup_evidence(candidate, result))
        session_frames.append(_session_evidence(candidate, result))

    candidates_frame = pd.DataFrame(candidate_rows)
    if not candidates_frame.empty:
        candidates_frame = candidates_frame.sort_values(["research_score", "candidate"], ascending=[False, True]).reset_index(drop=True)
    setup_report = _concat_or_empty(setup_frames)
    session_report = _concat_or_empty(session_frames)
    walk_summary, walk_rankings = _run_walk_forward_evidence(candles, candidate_defs, candidates_frame, cfg)
    promotion = _promotion_report(candidates_frame, walk_summary, cfg)
    return StrategyResearchResult(
        config=cfg,
        hypotheses=hypotheses,
        candidates=candidates_frame,
        setup_report=setup_report,
        session_report=session_report,
        walk_forward_summary=walk_summary,
        walk_forward_rankings=walk_rankings,
        promotion_report=promotion,
    )


def write_strategy_research_pack(result: StrategyResearchResult, output_dir: str | Path) -> StrategyResearchResult:
    """Write JSON, CSV, and HTML artifacts for a strategy research run."""

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    artifacts = StrategyResearchArtifacts(
        output_dir=root,
        summary_json=root / "summary.json",
        hypotheses_csv=root / "hypotheses.csv",
        candidates_csv=root / "candidates.csv",
        setup_report_csv=root / "setup_report.csv",
        session_report_csv=root / "session_report.csv",
        walk_forward_summary_csv=root / "walk_forward_summary.csv",
        walk_forward_rankings_csv=root / "walk_forward_rankings.csv",
        promotion_report_csv=root / "promotion_report.csv",
        html_report=root / "research_report.html",
    )
    _write_csv(result.hypotheses, artifacts.hypotheses_csv)
    _write_csv(result.candidates, artifacts.candidates_csv)
    _write_csv(result.setup_report, artifacts.setup_report_csv)
    _write_csv(result.session_report, artifacts.session_report_csv)
    _write_csv(result.walk_forward_summary, artifacts.walk_forward_summary_csv)
    _write_csv(result.walk_forward_rankings, artifacts.walk_forward_rankings_csv)
    _write_csv(result.promotion_report, artifacts.promotion_report_csv)
    result = result.with_artifacts(artifacts)
    artifacts.summary_json.write_text(json.dumps(_jsonable(result.to_safe_dict()), indent=2, sort_keys=True), encoding="utf-8")
    artifacts.html_report.write_text(render_strategy_research_report_html(result), encoding="utf-8")
    return result


def render_strategy_research_report_html(result: StrategyResearchResult) -> str:
    """Render a standalone HTML report for strategy research evidence."""

    top = _top_candidates(result.candidates, limit=15)
    status = "OK" if result.ok else "RESEARCH"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SMC TA Strategy Research Pack</title>
  <style>
    :root {{
      --ink: #182126;
      --muted: #5c6b73;
      --line: #d9e0dd;
      --surface: #ffffff;
      --band: #f4f6f5;
      --blue: #22577a;
      --green: #0f766e;
      --amber: #a15c07;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--band); color: var(--ink); font-family: Arial, Helvetica, sans-serif; }}
    header {{ background: #22313a; color: #fff; padding: 18px 24px; }}
    h1 {{ margin: 0; font-size: 24px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 10px; font-size: 16px; letter-spacing: 0; }}
    main {{ display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 14px; padding: 18px; }}
    section {{ background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 14px; min-width: 0; }}
    .span-12 {{ grid-column: span 12; }}
    .span-6 {{ grid-column: span 6; }}
    .meta {{ margin-top: 8px; color: #dce5e7; display: flex; flex-wrap: wrap; gap: 12px; font-size: 13px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }}
    .metric {{ min-height: 64px; background: #fbfcfc; border-left: 4px solid var(--blue); padding: 8px 10px; }}
    .metric:nth-child(2n) {{ border-left-color: var(--green); }}
    .metric span {{ color: var(--muted); display: block; font-size: 12px; text-transform: uppercase; }}
    .metric strong {{ display: block; margin-top: 5px; font-size: 18px; overflow-wrap: anywhere; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #edf1ef; padding: 7px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }}
    th {{ background: #f7f9f8; color: #34464d; }}
    .table-wrap {{ overflow-x: auto; }}
    .chart svg {{ width: 100%; height: 190px; display: block; }}
    .ok {{ color: var(--green); }}
    .research {{ color: var(--amber); }}
    @media (max-width: 900px) {{
      main {{ grid-template-columns: 1fr; padding: 12px; }}
      .span-12, .span-6 {{ grid-column: span 1; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>SMC TA Strategy Research Pack <span class="{escape(status.lower())}">{escape(status)}</span></h1>
    <div class="meta">
      <span>{escape(result.summary())}</span>
      <span>hypotheses: {len(result.hypotheses)}</span>
      <span>candidates: {len(result.candidates)}</span>
      <span>walk-forward: {"on" if result.config.run_walk_forward else "off"}</span>
    </div>
  </header>
  <main>
    <section class="span-12">
      <div class="metrics">
        {_metric("Demo Candidates", _status_count(result.promotion_report, "demo_candidate"))}
        {_metric("Research Only", _status_count(result.promotion_report, "research_only"))}
        {_metric("Blocked", _status_count(result.promotion_report, "blocked"))}
        {_metric("Best Candidate", _best_value(top, "candidate"))}
        {_metric("Best Score", _best_value(top, "research_score"))}
        {_metric("Best Return", _best_value(top, "total_return_percent"), suffix="%")}
      </div>
    </section>
    <section class="span-6 chart">
      <h2>Top Research Scores</h2>
      {_bar_svg(top, "candidate", "research_score")}
    </section>
    <section class="span-6 chart">
      <h2>Top Returns</h2>
      {_bar_svg(top, "candidate", "total_return_percent")}
    </section>
    <section class="span-12">
      <h2>Promotion Report</h2>
      {_frame_to_html(result.promotion_report)}
    </section>
    <section class="span-12">
      <h2>Top Candidates</h2>
      {_frame_to_html(top)}
    </section>
    <section class="span-12">
      <h2>Setup Evidence</h2>
      {_frame_to_html(result.setup_report.head(50))}
    </section>
    <section class="span-12">
      <h2>Session Evidence</h2>
      {_frame_to_html(result.session_report.head(50))}
    </section>
    <section class="span-12">
      <h2>Walk-Forward Evidence</h2>
      {_frame_to_html(result.walk_forward_summary)}
    </section>
  </main>
</body>
</html>
"""


def _expand_hypothesis_candidates(
    hypothesis: StrategyResearchHypothesis,
    symbol: str,
    base: BacktestConfig,
    profile_risk: RiskConfig,
    grid: StrategyResearchGrid,
) -> list[StrategyResearchCandidate]:
    confluence = base.confluence
    risk = base.risk if base.risk != RiskConfig() else profile_risk
    scores = _score_values(confluence.min_signal_score, grid.min_signal_score_offsets)
    adx_values = _float_values(confluence.adx_threshold, grid.adx_threshold_offsets, min_value=1.0)
    poi_values = _multiplied_values(confluence.max_poi_atr_distance, grid.max_poi_atr_distance_multipliers, min_value=0.05)
    spread_values = _spread_values(confluence.max_spread_pips, grid.max_spread_pips_multipliers)
    risk_values = _multiplied_values(risk.risk_percent_per_trade, grid.risk_percent_multipliers, min_value=0.01)

    out: list[StrategyResearchCandidate] = []
    for score in scores:
        for adx in adx_values:
            for poi in poi_values:
                for spread in spread_values:
                    for risk_percent in risk_values:
                        variant_index = len(out) + 1
                        candidate_confluence = replace(
                            confluence,
                            min_signal_score=score,
                            adx_threshold=adx,
                            max_poi_atr_distance=poi,
                            max_spread_pips=spread,
                        )
                        candidate_risk = replace(risk, risk_percent_per_trade=risk_percent)
                        candidate_config = replace(
                            base,
                            symbol=symbol.upper(),
                            confluence=candidate_confluence,
                            risk=candidate_risk,
                        )
                        parameters = {
                            "min_signal_score": score,
                            "adx_threshold": adx,
                            "max_poi_atr_distance": poi,
                            "max_spread_pips": spread,
                            "risk_percent_per_trade": risk_percent,
                            "session_filter": candidate_config.session_filter,
                        }
                        out.append(
                            StrategyResearchCandidate(
                                name=_candidate_name(hypothesis.name, symbol, variant_index),
                                hypothesis_name=hypothesis.name,
                                profile_name=hypothesis.profile_name,
                                symbol=symbol.upper(),
                                config=candidate_config,
                                parameters=parameters,
                                thesis=hypothesis.thesis,
                                tags=hypothesis.tags,
                            )
                        )
                        if len(out) >= grid.max_candidates_per_hypothesis:
                            return out
    return out


def _candidate_metrics(candidate: StrategyResearchCandidate, result: BacktestResult) -> dict[str, Any]:
    metrics = performance_summary(result.equity_curve, result.trades if not result.trades.empty else None)
    row = {
        "candidate": candidate.name,
        "hypothesis": candidate.hypothesis_name,
        "profile": candidate.profile_name,
        "symbol": candidate.symbol,
        "status": "ok",
        "message": "backtest_completed",
        "final_balance": result.final_balance,
        "final_equity": result.final_equity,
        **metrics,
        **{f"param_{key}": value for key, value in candidate.parameters.items()},
    }
    row["research_score"] = _research_score(row)
    return row


def _setup_evidence(candidate: StrategyResearchCandidate, result: BacktestResult) -> pd.DataFrame:
    setups = classify_smc_setups(result.features, result.signals)
    signals = result.signals.join(setups)
    active = signals[signals["side"].isin(["long", "short"])]
    signal_report = _setup_signal_report(active)
    trade_report = _setup_trade_report(result.trades, setups)
    report = _merge_setup_reports(signal_report, trade_report)
    report.insert(0, "symbol", candidate.symbol)
    report.insert(0, "profile", candidate.profile_name)
    report.insert(0, "hypothesis", candidate.hypothesis_name)
    report.insert(0, "candidate", candidate.name)
    return report


def _session_evidence(candidate: StrategyResearchCandidate, result: BacktestResult) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    signal_sessions = _signal_session_counts(result.signals)
    trade_sessions = _trade_session_metrics(result.trades)
    for session in sorted(set(signal_sessions) | set(trade_sessions)):
        row = {
            "candidate": candidate.name,
            "hypothesis": candidate.hypothesis_name,
            "profile": candidate.profile_name,
            "symbol": candidate.symbol,
            "session": session,
            **signal_sessions.get(session, {}),
            **trade_sessions.get(session, {}),
        }
        rows.append(row)
    return pd.DataFrame(rows)


def _run_walk_forward_evidence(
    candles: Mapping[str, pd.DataFrame],
    candidate_defs: tuple[StrategyResearchCandidate, ...],
    candidate_report: pd.DataFrame,
    cfg: StrategyResearchConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not cfg.run_walk_forward or candidate_report.empty:
        return pd.DataFrame(), pd.DataFrame()
    rows: list[dict[str, Any]] = []
    rankings: list[pd.DataFrame] = []
    wf_config = cfg.walk_forward or WalkForwardConfig(train_size=500, test_size=150, require_trades=True)
    candidate_map = {candidate.name: candidate for candidate in candidate_defs}
    top = _top_for_walk_forward(candidate_report, cfg.max_walk_forward_candidates)
    for (symbol, hypothesis), frame in top.groupby(["symbol", "hypothesis"], dropna=False):
        selected_candidates = [candidate_map[name] for name in frame["candidate"] if name in candidate_map]
        if not selected_candidates:
            continue
        data = candles.get(str(symbol).upper())
        if data is None:
            continue
        try:
            result = run_walk_forward(
                data,
                candidates=[candidate.walk_forward_candidate() for candidate in selected_candidates],
                config=wf_config,
            )
            metrics = (
                performance_summary(result.combined_equity_curve, result.combined_trades if not result.combined_trades.empty else None)
                if not result.combined_equity_curve.empty
                else {}
            )
            rows.append(
                {
                    "symbol": symbol,
                    "hypothesis": hypothesis,
                    "status": "ok",
                    "folds": len(result.folds),
                    "selected_candidates": ";".join(result.selected_candidates),
                    **{f"walk_forward_{key}": value for key, value in metrics.items()},
                }
            )
            ranking = result.candidate_rankings.copy()
            ranking.insert(0, "symbol", symbol)
            ranking.insert(1, "hypothesis", hypothesis)
            rankings.append(ranking)
        except Exception as exc:
            rows.append(
                {
                    "symbol": symbol,
                    "hypothesis": hypothesis,
                    "status": "failed",
                    "folds": 0,
                    "selected_candidates": "",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )
    return pd.DataFrame(rows), _concat_or_empty(rankings)


def _promotion_report(
    candidates: pd.DataFrame,
    walk_forward_summary: pd.DataFrame,
    cfg: StrategyResearchConfig,
) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    wf_lookup = _walk_forward_lookup(walk_forward_summary)
    rows: list[dict[str, Any]] = []
    for _, row in candidates.iterrows():
        candidate = str(row["candidate"])
        reasons: list[str] = []
        status = "demo_candidate"
        trades = _number(row.get("trades"), default=0.0)
        drawdown = abs(_number(row.get("max_drawdown_percent"), default=0.0))
        profit_factor = _number(row.get("profit_factor"), default=0.0, inf_value=999.0)
        total_return = _number(row.get("total_return_percent"), default=0.0)
        win_rate = _number(row.get("win_rate_percent"), default=0.0)
        if str(row.get("status")) != "ok":
            status = "blocked"
            reasons.append(str(row.get("message") or "candidate_failed"))
        if trades < cfg.min_trades:
            status = "research_only" if status != "blocked" else status
            reasons.append(f"trades_below_min:{int(trades)}<{cfg.min_trades}")
        if drawdown > cfg.max_drawdown_percent:
            status = "blocked"
            reasons.append(f"drawdown_above_limit:{drawdown:.2f}>{cfg.max_drawdown_percent:.2f}")
        if profit_factor < cfg.min_profit_factor:
            status = "research_only" if status != "blocked" else status
            reasons.append(f"profit_factor_below_min:{profit_factor:.2f}<{cfg.min_profit_factor:.2f}")
        if total_return < cfg.min_total_return_percent:
            status = "research_only" if status != "blocked" else status
            reasons.append(f"return_below_min:{total_return:.2f}<{cfg.min_total_return_percent:.2f}")
        if cfg.min_win_rate_percent is not None and win_rate < cfg.min_win_rate_percent:
            status = "research_only" if status != "blocked" else status
            reasons.append(f"win_rate_below_min:{win_rate:.2f}<{cfg.min_win_rate_percent:.2f}")

        wf_key = (str(row["symbol"]), str(row["hypothesis"]))
        wf = wf_lookup.get(wf_key)
        if cfg.run_walk_forward:
            if wf is None or wf.get("status") != "ok":
                status = "research_only" if status != "blocked" else status
                reasons.append("walk_forward_missing_or_failed")
            elif _number(wf.get("walk_forward_total_return_percent"), default=0.0) < cfg.min_total_return_percent:
                status = "research_only" if status != "blocked" else status
                reasons.append("walk_forward_return_below_min")

        rows.append(
            {
                "candidate": candidate,
                "hypothesis": row["hypothesis"],
                "profile": row["profile"],
                "symbol": row["symbol"],
                "research_status": status,
                "promotion_reasons": ";".join(reasons or ["passes_research_gates"]),
                "research_score": row.get("research_score"),
                "trades": trades,
                "total_return_percent": total_return,
                "max_drawdown_percent": row.get("max_drawdown_percent"),
                "profit_factor": row.get("profit_factor"),
                "win_rate_percent": row.get("win_rate_percent"),
            }
        )
    out = pd.DataFrame(rows)
    out["status_rank"] = out["research_status"].map({"demo_candidate": 0, "research_only": 1, "blocked": 2}).fillna(3)
    out = out.sort_values(["status_rank", "research_score"], ascending=[True, False]).drop(columns=["status_rank"])
    return out.reset_index(drop=True)


def _setup_signal_report(active_signals: pd.DataFrame) -> pd.DataFrame:
    if active_signals.empty:
        return pd.DataFrame(columns=["setup_name", "signals", "long_signals", "short_signals", "average_setup_score"])
    return (
        active_signals.groupby("setup_name", dropna=False)
        .agg(
            signals=("side", "count"),
            long_signals=("side", lambda values: int((values == "long").sum())),
            short_signals=("side", lambda values: int((values == "short").sum())),
            average_setup_score=("setup_score", "mean"),
        )
        .reset_index()
    )


def _setup_trade_report(trades: pd.DataFrame, setups: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "opened_at" not in trades.columns:
        return pd.DataFrame(columns=["setup_name", "trades", "net_pnl", "average_pnl", "win_rate_percent"])
    enriched = trades.copy()
    enriched["setup_name"] = _lookup_setup_names(enriched["opened_at"], setups)
    pnl = enriched["realized_pnl"].fillna(0.0)
    enriched["realized_pnl"] = pnl
    return (
        enriched.groupby("setup_name", dropna=False)
        .agg(
            trades=("position_id", "count"),
            net_pnl=("realized_pnl", "sum"),
            average_pnl=("realized_pnl", "mean"),
            win_rate_percent=("realized_pnl", lambda values: float((values > 0).mean() * 100.0) if len(values) else 0.0),
        )
        .reset_index()
    )


def _merge_setup_reports(signal_report: pd.DataFrame, trade_report: pd.DataFrame) -> pd.DataFrame:
    if signal_report.empty and trade_report.empty:
        return pd.DataFrame(
            [
                {
                    "setup_name": "none",
                    "signals": 0,
                    "long_signals": 0,
                    "short_signals": 0,
                    "average_setup_score": 0.0,
                    "trades": 0,
                    "net_pnl": 0.0,
                    "average_pnl": 0.0,
                    "win_rate_percent": 0.0,
                }
            ]
        )
    if signal_report.empty:
        out = trade_report.copy()
    elif trade_report.empty:
        out = signal_report.copy()
    else:
        out = signal_report.merge(trade_report, on="setup_name", how="outer")
    for column in ("signals", "long_signals", "short_signals", "average_setup_score", "trades", "net_pnl", "average_pnl", "win_rate_percent"):
        if column not in out.columns:
            out[column] = 0
    return out.fillna(0).sort_values(["trades", "signals", "setup_name"], ascending=[False, False, True]).reset_index(drop=True)


def _signal_session_counts(signals: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if signals.empty or not isinstance(signals.index, pd.DatetimeIndex):
        return {}
    active = signals[signals["side"].isin(["long", "short"])]
    sessions = session_labels(active.index) if not active.empty else session_labels(signals.index[:0])
    out: dict[str, dict[str, Any]] = {}
    for column in sessions.columns:
        selected = active.loc[sessions[column].to_numpy()]
        out[column] = {
            "signals": int(len(selected)),
            "long_signals": int((selected["side"] == "long").sum()) if not selected.empty else 0,
            "short_signals": int((selected["side"] == "short").sum()) if not selected.empty else 0,
        }
    return out


def _trade_session_metrics(trades: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if trades.empty or "opened_at" not in trades.columns:
        return {}
    opened = pd.DatetimeIndex(pd.to_datetime(trades["opened_at"], utc=True))
    sessions = session_labels(opened)
    out: dict[str, dict[str, Any]] = {}
    frame = trades.reset_index(drop=True).copy()
    frame["realized_pnl"] = frame["realized_pnl"].fillna(0.0)
    for column in sessions.columns:
        selected = frame.loc[sessions[column].to_numpy()]
        pnl = selected["realized_pnl"] if not selected.empty else pd.Series(dtype=float)
        out[column] = {
            "trades": int(len(selected)),
            "net_pnl": float(pnl.sum()) if len(pnl) else 0.0,
            "win_rate_percent": float((pnl > 0).mean() * 100.0) if len(pnl) else 0.0,
        }
    return out


def _lookup_setup_names(opened_at: pd.Series, setups: pd.DataFrame) -> list[str]:
    if setups.empty or not isinstance(setups.index, pd.DatetimeIndex):
        return ["unknown"] * len(opened_at)
    lookup = setups.sort_index()
    timestamps = pd.DatetimeIndex(pd.to_datetime(opened_at, utc=True))
    positions = lookup.index.get_indexer(timestamps, method="pad")
    names: list[str] = []
    for position in positions:
        if position < 0:
            names.append("unknown")
        else:
            names.append(str(lookup.iloc[position].get("setup_name", "unknown")))
    return names


def _top_for_walk_forward(candidates: pd.DataFrame, limit: int) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    ok = candidates[candidates["status"] == "ok"].copy()
    if ok.empty:
        return pd.DataFrame()
    return (
        ok.sort_values(["symbol", "hypothesis", "research_score"], ascending=[True, True, False])
        .groupby(["symbol", "hypothesis"], group_keys=False)
        .head(limit)
    )


def _walk_forward_lookup(summary: pd.DataFrame) -> dict[tuple[str, str], dict[str, Any]]:
    if summary.empty:
        return {}
    return {
        (str(row["symbol"]), str(row["hypothesis"])): row.to_dict()
        for _, row in summary.iterrows()
    }


def _research_score(row: Mapping[str, Any]) -> float:
    total_return = _number(row.get("total_return_percent"), default=0.0)
    drawdown = abs(_number(row.get("max_drawdown_percent"), default=0.0))
    profit_factor = min(_number(row.get("profit_factor"), default=0.0, inf_value=5.0), 5.0)
    trades = min(_number(row.get("trades"), default=0.0), 25.0) / 25.0
    return float(total_return / max(drawdown, 1.0) + profit_factor * 0.25 + trades * 0.25)


def _missing_data_row(candidate: StrategyResearchCandidate) -> dict[str, Any]:
    return {
        "candidate": candidate.name,
        "hypothesis": candidate.hypothesis_name,
        "profile": candidate.profile_name,
        "symbol": candidate.symbol,
        "status": "missing_data",
        "message": f"no candles supplied for {candidate.symbol}",
        "research_score": -1e9,
        **{f"param_{key}": value for key, value in candidate.parameters.items()},
    }


def _hypotheses_frame(hypotheses: tuple[StrategyResearchHypothesis, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "hypothesis": item.name,
                "profile": item.profile_name,
                "thesis": item.thesis,
                "expected_setups": ";".join(item.expected_setups),
                "symbols": ";".join(item.symbols),
                "tags": ";".join(item.tags),
                "notes": item.notes,
            }
            for item in hypotheses
        ]
    )


def _candles_mapping(
    candles_by_symbol: Mapping[str, pd.DataFrame] | pd.DataFrame,
    cfg: StrategyResearchConfig,
) -> dict[str, pd.DataFrame]:
    if isinstance(candles_by_symbol, pd.DataFrame):
        symbol = (cfg.symbols[0] if cfg.symbols else "EURUSD").upper()
        return {symbol: normalize_ohlcv(candles_by_symbol)}
    return {symbol.upper(): normalize_ohlcv(candles) for symbol, candles in candles_by_symbol.items()}


def _score_values(base: int, offsets: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(sorted({max(1, int(base + offset)) for offset in offsets}))


def _float_values(base: float, offsets: tuple[float, ...], *, min_value: float) -> tuple[float, ...]:
    return tuple(sorted({round(max(min_value, float(base + offset)), 4) for offset in offsets}))


def _multiplied_values(base: float, multipliers: tuple[float, ...], *, min_value: float) -> tuple[float, ...]:
    return tuple(sorted({round(max(min_value, float(base * multiplier)), 4) for multiplier in multipliers}))


def _spread_values(base: float | None, multipliers: tuple[float, ...]) -> tuple[float | None, ...]:
    if base is None:
        return (None,)
    return _multiplied_values(base, multipliers, min_value=0.1)


def _candidate_name(hypothesis: str, symbol: str, index: int) -> str:
    return f"{_safe_name(hypothesis)}_{symbol.upper()}_v{index:02d}"


def _safe_name(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")


def _number(value: Any, *, default: float, inf_value: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isinf(number):
        return inf_value if inf_value is not None else number
    if math.isnan(number):
        return default
    return number


def _concat_or_empty(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [frame for frame in frames if not frame.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _top_candidates(candidates: pd.DataFrame, *, limit: int) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    return candidates.sort_values(["research_score", "candidate"], ascending=[False, True]).head(limit)


def _status_count(frame: pd.DataFrame, status: str) -> int:
    if frame.empty or "research_status" not in frame.columns:
        return 0
    return int(frame["research_status"].eq(status).sum())


def _best_value(frame: pd.DataFrame, column: str) -> Any:
    if frame.empty or column not in frame.columns:
        return None
    return frame.iloc[0][column]


def _metric(label: str, value: Any, *, suffix: str = "") -> str:
    return f"<div class=\"metric\"><span>{escape(label)}</span><strong>{escape(_format_value(value, suffix=suffix))}</strong></div>"


def _format_value(value: Any, *, suffix: str = "") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    if isinstance(value, float):
        if math.isinf(value):
            text = "inf"
        else:
            text = f"{value:.2f}"
    else:
        text = str(value)
    return f"{text}{suffix}"


def _frame_to_html(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "<p>No rows.</p>"
    visible = frame.copy()
    for column in visible.columns:
        visible[column] = visible[column].map(lambda value: _format_value(value) if isinstance(value, float) else value)
    return '<div class="table-wrap">' + visible.to_html(index=False, escape=True, border=0) + "</div>"


def _bar_svg(frame: pd.DataFrame, label_col: str, value_col: str) -> str:
    if frame.empty or label_col not in frame.columns or value_col not in frame.columns:
        return "<p>No chart data.</p>"
    values = [_number(value, default=0.0, inf_value=0.0) for value in frame[value_col]]
    if not values:
        return "<p>No chart data.</p>"
    labels = [str(value) for value in frame[label_col]]
    width = 760
    height = 190
    left = 145
    row_height = max(18, min(26, int((height - 20) / max(len(values), 1))))
    chart_height = row_height * len(values) + 20
    max_value = max(abs(value) for value in values) or 1.0
    bars: list[str] = []
    for i, (label, value) in enumerate(zip(labels, values)):
        y = 12 + i * row_height
        bar_width = int((abs(value) / max_value) * (width - left - 30))
        color = "#0f766e" if value >= 0 else "#a15c07"
        bars.append(f'<text x="4" y="{y + 12}" font-size="11" fill="#405158">{escape(label[:24])}</text>')
        bars.append(f'<rect x="{left}" y="{y}" width="{bar_width}" height="12" fill="{color}"></rect>')
        bars.append(f'<text x="{left + bar_width + 4}" y="{y + 11}" font-size="11" fill="#405158">{value:.2f}</text>')
    return f'<svg viewBox="0 0 {width} {chart_height}" role="img">{"".join(bars)}</svg>'


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [_jsonable(row) for row in frame.to_dict(orient="records")]


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    pd.DataFrame(frame).to_csv(path, index=False)


def _bundle_dict(bundle: StrategyResearchArtifacts | None) -> dict[str, str] | None:
    if bundle is None:
        return None
    return {key: str(value) for key, value in asdict(bundle).items()}


def _config_dict(config: StrategyResearchConfig) -> dict[str, Any]:
    out = asdict(config)
    walk_forward = config.walk_forward
    if walk_forward is not None:
        out["walk_forward"] = asdict(walk_forward)
    return out


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return str(value)
    return value
