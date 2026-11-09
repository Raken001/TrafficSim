"""Parallel experiment orchestrator.

Distributes independent SUMO/TraCI simulations across CPU cores using
multiprocessing.Pool. Each worker process gets its own TraCI connection
to a separate SUMO instance and returns DataFrames (never touches the
database). The master process bulk-inserts all results in a single
SQLite transaction, eliminating lock contention.

Usage:
    python orchestrator.py
"""

import multiprocessing
import sys
from datetime import datetime

from controller import TrafficExperiment, bulk_insert_results
from strategies import FixedTimeStrategy, AdaptiveStrategy
from process_data import ResultsProcessor

# ---------------------------------------------------------------------------
# Strategy configurations (plain dicts for safe pickling across processes)
# ---------------------------------------------------------------------------
STRATEGY_CONFIGS = [
    {"type": "fixed_time", "name": "fixed_time"},
    {"type": "adaptive", "min_green": 15, "max_green": 60, "name": "adaptive"},
    {"type": "adaptive", "min_green": 10, "max_green": 45, "name": "adaptive_tuned"},
]


def _build_strategy(config: dict):
    """Reconstructs a SignalStrategy from a picklable config dict."""
    if config["type"] == "fixed_time":
        return FixedTimeStrategy()
    return AdaptiveStrategy(
        min_green=config["min_green"],
        max_green=config["max_green"],
        name=config["name"],
    )


# ---------------------------------------------------------------------------
# Worker function (one per Pool process)
# ---------------------------------------------------------------------------
def _run_single_simulation(args: dict) -> dict | None:
    """Top-level worker function executed by each Pool process.

    Each worker gets its own Python process (and therefore its own TraCI
    connection to a separate SUMO instance).  Returns a dict containing
    the metrics and trips DataFrames for centralized bulk insertion.
    """
    strategy = _build_strategy(args["strategy_config"])
    experiment = TrafficExperiment("baseline.net.xml", "traffic.rou.xml")
    return experiment.run(
        strategy=strategy,
        imbalance=args["imbalance"],
        seed=args["seed"],
        batch_id=args["batch_id"],
    )


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
def run_experiments():
    imbalances = [0.5, 0.7, 0.9]
    seeds = list(range(42, 92))  # 50 seeds: 42 .. 91
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Build task list: one dict per simulation run
    tasks = [
        {
            "strategy_config": sc,
            "imbalance": imb,
            "seed": s,
            "batch_id": batch_id,
        }
        for imb in imbalances
        for s in seeds
        for sc in STRATEGY_CONFIGS
    ]

    num_workers = min(multiprocessing.cpu_count(), 8)
    print(f"=== Batch {batch_id}: {len(tasks)} runs on {num_workers} workers ===\n")

    # --- Parallel execution ---
    with multiprocessing.Pool(processes=num_workers) as pool:
        results = []
        for i, result in enumerate(pool.imap_unordered(_run_single_simulation, tasks), 1):
            results.append(result)
            sys.stdout.write(f"\r  Progress: {i}/{len(tasks)} runs complete")
            sys.stdout.flush()
    print("\n")

    # --- Centralized bulk insert (single connection, single transaction) ---
    bulk_insert_results(results, batch_id)

    # --- Post-processing: aggregation, statistics, and comparison plots ---
    print("\n--- Running data processing pipeline ---")
    processor = ResultsProcessor(batch_id=batch_id)
    processor.run(make_plots=True)


if __name__ == "__main__":
    run_experiments()
