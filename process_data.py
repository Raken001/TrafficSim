"""Aggregated data processing pipeline.

Reads raw simulation output from the SQLite database, reduces it to one summary
row per run, aggregates those runs across random seeds, and performs a paired
fixed-time vs. adaptive comparison for each traffic imbalance condition.

Raw input tables (written by controller.py):
    simulation_metrics  - one row per simulation step (queues, edge waiting, throughput)
    vehicle_trips       - one row per vehicle (SUMO tripinfo: waiting time, time loss)

Derived output tables (written here, fully recomputed on every run):
    run_summary         - one row per (strategy, imbalance, seed)
    condition_summary   - mean / std / 95% CI per (strategy, imbalance, metric)
    paired_comparison   - adaptive vs. fixed-time paired by seed, per (imbalance, metric)

The derived tables are also exported as CSV files to the results/ directory.
"""

import os
import sqlite3

import numpy as np
import pandas as pd
from scipy import stats

DB_FILE = "research_data.db"
RESULTS_DIR = "results"

# Only these strategy labels are part of the experiment. Older development rows
# (e.g. strategy="fixed") are ignored rather than deleted.
VALID_STRATEGIES = ("fixed_time", "adaptive")
BASELINE_STRATEGY = "fixed_time"
TREATMENT_STRATEGY = "adaptive"

RUN_KEYS = ["strategy", "imbalance", "seed"]

# Per-run metrics that get aggregated across seeds, with human-readable labels.
# NS is the major axis whenever imbalance > 0.5; EW is the minor axis.
METRICS = {
    "avg_delay":         "Average vehicle delay (s)",
    "avg_waiting_time":  "Average waiting time (s)",
    "ns_avg_delay":      "NS average delay (s)",
    "ew_avg_delay":      "EW average delay (s)",
    "ew_ns_delay_ratio": "EW/NS delay ratio",
    "max_wait":          "Max individual wait (s)",
    "ns_max_wait":       "NS max individual wait (s)",
    "ew_max_wait":       "EW max individual wait (s)",
    "mean_queue":        "Mean queue length (vehicles)",
    "max_queue":         "Max queue length (vehicles)",
    "throughput":        "Throughput (vehicles)",
    "vehicles":          "Vehicles inserted",
    "unfinished":        "Vehicles unfinished at end",
}


class ResultsProcessor:
    """Turns raw per-step and per-vehicle simulation data into research statistics."""

    def __init__(self, db_file: str = DB_FILE, results_dir: str = RESULTS_DIR, batch_id: str = None):
        """
        :param db_file: Path to the SQLite database produced by controller.py
        :param results_dir: Directory where CSV summaries and figures are written
        :param batch_id: If given, only process data from this batch. If None,
                         auto-detects the latest batch_id in the database.
        """
        self.db_file = db_file
        self.results_dir = results_dir
        self.batch_id = batch_id

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    def _resolve_batch_id(self) -> str | None:
        """Auto-detects the latest batch_id if none was explicitly provided."""
        if self.batch_id:
            return self.batch_id
        with sqlite3.connect(self.db_file) as conn:
            try:
                result = conn.execute("SELECT MAX(batch_id) FROM simulation_metrics").fetchone()
                batch_id = result[0] if result and result[0] else None
                if batch_id:
                    print(f"Auto-detected latest batch: {batch_id}")
                return batch_id
            except sqlite3.OperationalError:
                return None

    def _read_table(self, table: str, batch_id: str = None) -> pd.DataFrame:
        """Reads rows for the experiment strategies, optionally filtered by batch_id.
        
        Returns empty frame if table is missing.
        """
        placeholders = ",".join("?" for _ in VALID_STRATEGIES)
        query = f"SELECT * FROM {table} WHERE strategy IN ({placeholders})"
        params = list(VALID_STRATEGIES)

        if batch_id:
            query += " AND batch_id = ?"
            params.append(batch_id)

        with sqlite3.connect(self.db_file) as conn:
            try:
                return pd.read_sql_query(query, conn, params=params)
            except pd.errors.DatabaseError:
                return pd.DataFrame()

    def load_step_metrics(self, batch_id: str = None) -> pd.DataFrame:
        return self._read_table("simulation_metrics", batch_id)

    def load_vehicle_trips(self, batch_id: str = None) -> pd.DataFrame:
        return self._read_table("vehicle_trips", batch_id)

    # ------------------------------------------------------------------ #
    # Stage 1: one row per run
    # ------------------------------------------------------------------ #
    @staticmethod
    def summarize_runs(steps: pd.DataFrame, trips: pd.DataFrame) -> pd.DataFrame:
        """Collapses each (strategy, imbalance, seed) run into a single row of metrics.
        
        Aggregating per run first (instead of pooling all steps) is what makes the
        later cross-seed statistics valid: each seed contributes one independent
        observation rather than hundreds of correlated time steps.
        """
        # --- Step-level metrics: queues and throughput ---
        steps = steps.copy()
        steps["total_queue"] = steps[["north_queue", "south_queue", "east_queue", "west_queue"]].sum(axis=1)
        step_summary = steps.groupby(RUN_KEYS).agg(
            mean_queue=("total_queue", "mean"),
            max_queue=("total_queue", "max"),
            throughput=("throughput", "sum"),
        ).reset_index()

        if trips.empty:
            print("Warning: vehicle_trips table is empty; delay and fairness metrics will be missing.")
            return step_summary

        # --- Vehicle-level metrics: delay and waiting (whole intersection) ---
        trip_summary = trips.groupby(RUN_KEYS).agg(
            vehicles=("vehicle_id", "count"),
            unfinished=("finished", lambda f: int((f == 0).sum())),
            avg_delay=("time_loss", "mean"),
            avg_waiting_time=("waiting_time", "mean"),
            max_wait=("waiting_time", "max"),
        ).reset_index()

        # --- Vehicle-level metrics per axis (directional fairness) ---
        # Instead of pivot_table with dict aggfunc (which errors on some pandas versions),
        # we groupby axis and aggregate, then unstack the axis.
        axis_summary = trips.groupby(RUN_KEYS + ["axis"]).agg(
            avg_delay=("time_loss", "mean"),
            max_wait=("waiting_time", "max")
        ).unstack("axis")
        
        # Flatten multi-level columns (e.g., 'avg_delay', 'EW' -> 'ew_avg_delay')
        axis_summary.columns = [f"{axis.lower()}_{metric}" for metric, axis in axis_summary.columns]
        axis_summary = axis_summary.reset_index()

        runs = (step_summary
                .merge(trip_summary, on=RUN_KEYS, how="left")
                .merge(axis_summary, on=RUN_KEYS, how="left"))

        # Ratio > 1 means the EW (minor) approach waits longer per vehicle than NS (major)
        runs["ew_ns_delay_ratio"] = np.where(
            runs["ns_avg_delay"] > 0, runs["ew_avg_delay"] / runs["ns_avg_delay"], np.nan
        )

        missing = runs["vehicles"].isna().sum()
        if missing:
            print(f"Warning: {missing} run(s) have step data but no vehicle trip data. Re-run the orchestrator.")

        ordered = RUN_KEYS + [m for m in METRICS if m in runs.columns]
        return runs[ordered].sort_values(RUN_KEYS).reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # Stage 2: aggregate across seeds
    # ------------------------------------------------------------------ #
    @staticmethod
    def summarize_conditions(runs: pd.DataFrame) -> pd.DataFrame:
        """Computes mean, standard deviation, and 95% confidence interval across seeds.
        
        Returns a long-format table: one row per (strategy, imbalance, metric).
        The CI half-width uses the t-distribution, appropriate for small samples.
        """
        metric_cols = [m for m in METRICS if m in runs.columns]
        long = runs.melt(id_vars=RUN_KEYS, value_vars=metric_cols,
                         var_name="metric", value_name="value").dropna(subset=["value"])

        summary = long.groupby(["strategy", "imbalance", "metric"])["value"].agg(
            mean="mean", std="std", n="count"
        ).reset_index()

        t_crit = stats.t.ppf(0.975, df=(summary["n"] - 1).clip(lower=1))
        summary["ci95"] = np.where(summary["n"] > 1, t_crit * summary["std"] / np.sqrt(summary["n"]), np.nan)
        summary["label"] = summary["metric"].map(METRICS)
        return summary.sort_values(["metric", "imbalance", "strategy"]).reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # Stage 3: paired comparison (same seeds, both strategies)
    # ------------------------------------------------------------------ #
    @staticmethod
    def compare_strategies(runs: pd.DataFrame) -> pd.DataFrame:
        """Paired adaptive vs. fixed-time comparison for each imbalance and metric.
        
        Because both strategies were run on identical seeds, each seed forms a
        matched pair. The paired t-test removes the seed-to-seed variation shared
        by both strategies, isolating the effect of the control algorithm.
        """
        metric_cols = [m for m in METRICS if m in runs.columns]
        rows = []
        for imbalance, group in runs.groupby("imbalance"):
            for metric in metric_cols:
                paired = group.pivot(index="seed", columns="strategy", values=metric)
                if BASELINE_STRATEGY not in paired or TREATMENT_STRATEGY not in paired:
                    continue
                paired = paired[[BASELINE_STRATEGY, TREATMENT_STRATEGY]].dropna()
                if paired.empty:
                    continue

                fixed = paired[BASELINE_STRATEGY]
                adaptive = paired[TREATMENT_STRATEGY]
                diff = adaptive - fixed
                fixed_mean = fixed.mean()

                if len(paired) > 1 and diff.std() > 0:
                    t_stat, p_value = stats.ttest_rel(adaptive, fixed)
                else:
                    t_stat, p_value = np.nan, np.nan  # identical pairs: test undefined

                rows.append({
                    "imbalance": imbalance,
                    "metric": metric,
                    "label": METRICS[metric],
                    "n_pairs": len(paired),
                    "fixed_mean": fixed_mean,
                    "adaptive_mean": adaptive.mean(),
                    "mean_diff": diff.mean(),
                    "pct_change": (diff.mean() / fixed_mean * 100) if fixed_mean != 0 else np.nan,
                    "t_stat": t_stat,
                    "p_value": p_value,
                    "significant": bool(p_value < 0.05) if not np.isnan(p_value) else False,
                })
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def save_results(self, runs: pd.DataFrame, conditions: pd.DataFrame, comparison: pd.DataFrame) -> None:
        """Writes derived tables to SQLite (replacing old versions) and to CSV in results/."""
        os.makedirs(self.results_dir, exist_ok=True)
        tables = {
            "run_summary": runs,
            "condition_summary": conditions,
            "paired_comparison": comparison,
        }
        with sqlite3.connect(self.db_file) as conn:
            for name, df in tables.items():
                # Derived data is fully recomputed each time, so replacing is idempotent
                df.to_sql(name, conn, if_exists="replace", index=False)
                df.to_csv(os.path.join(self.results_dir, f"{name}.csv"), index=False)
        print(f"Saved run_summary ({len(runs)} rows), condition_summary ({len(conditions)} rows), "
              f"paired_comparison ({len(comparison)} rows) to {self.db_file} and {self.results_dir}/")

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #
    @staticmethod
    def print_headline(comparison: pd.DataFrame) -> None:
        """Prints a compact console table of the key comparison metrics."""
        key_metrics = ["avg_delay", "ns_avg_delay", "ew_avg_delay", "ew_ns_delay_ratio",
                       "max_wait", "max_queue", "throughput"]
        view = comparison[comparison["metric"].isin(key_metrics)].copy()
        if view.empty:
            return
        view["metric"] = pd.Categorical(view["metric"], categories=key_metrics, ordered=True)
        view = view.sort_values(["imbalance", "metric"])
        cols = ["imbalance", "metric", "fixed_mean", "adaptive_mean", "pct_change", "p_value"]
        print("\n=== Adaptive vs. Fixed-Time (paired by seed) ===")
        print(view[cols].to_string(index=False, float_format=lambda x: f"{x:,.3f}"))

    # ------------------------------------------------------------------ #
    # Pipeline entry point
    # ------------------------------------------------------------------ #
    def run(self, make_plots: bool = True) -> dict:
        """Executes the full processing pipeline and returns the derived DataFrames."""
        batch_id = self._resolve_batch_id()
        if batch_id:
            print(f"Processing batch: {batch_id}")
        else:
            print("Processing all batches (no batch_id filter).")

        steps = self.load_step_metrics(batch_id)
        if steps.empty:
            print(f"No simulation data found in {self.db_file}. Run orchestrator.py first.")
            return {}
        trips = self.load_vehicle_trips(batch_id)

        runs = self.summarize_runs(steps, trips)
        conditions = self.summarize_conditions(runs)
        comparison = self.compare_strategies(runs)

        self.save_results(runs, conditions, comparison)
        self.print_headline(comparison)

        if make_plots:
            # Imported here so the processing layer has no hard dependency on matplotlib
            from analyze_metrics import generate_comparison_plots
            generate_comparison_plots(conditions, self.results_dir)

        return {"runs": runs, "conditions": conditions, "comparison": comparison}


if __name__ == "__main__":
    ResultsProcessor().run()
