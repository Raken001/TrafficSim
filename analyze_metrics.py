import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import sqlite3
import os

def generate_research_plots(strategy: str = "fixed", imbalance: float = 0.5, seed: int = 42, db_file: str = "research_data.db", batch_id: str = None):
    """Reads simulation metrics from SQLite database and generates visualizations for research analysis."""
    
    if not os.path.exists(db_file):
        print(f"Error: Database file '{db_file}' not found. Please run controller.py first.")
        return

    # Query the database for this specific run (filtered by batch to avoid cross-batch duplicate steps)
    conn = sqlite3.connect(db_file)
    if batch_id:
        query = f"SELECT * FROM simulation_metrics WHERE strategy='{strategy}' AND imbalance={imbalance} AND seed={seed} AND batch_id='{batch_id}'"
    else:
        try:
            has_batch = conn.execute("SELECT 1 FROM simulation_metrics WHERE batch_id IS NOT NULL LIMIT 1").fetchone()
            if has_batch:
                query = f"SELECT * FROM simulation_metrics WHERE strategy='{strategy}' AND imbalance={imbalance} AND seed={seed} AND batch_id = (SELECT MAX(batch_id) FROM simulation_metrics)"
            else:
                query = f"SELECT * FROM simulation_metrics WHERE strategy='{strategy}' AND imbalance={imbalance} AND seed={seed}"
        except Exception:
            query = f"SELECT * FROM simulation_metrics WHERE strategy='{strategy}' AND imbalance={imbalance} AND seed={seed}"

    df = pd.read_sql_query(query, conn)
    conn.close()

    if df.empty:
        print(f"No metrics found for strategy='{strategy}', imbalance={imbalance}, seed={seed}.")
        return

    # Initialize a 3-panel vertical figure
    fig, axes = plt.subplots(3, 1, figsize=(10, 15))
    
    # 1. Queue Length (Congestion Severity)
    axes[0].plot(df['step'], df['north_queue'] + df['south_queue'], label='North/South Axis', color='blue')
    axes[0].plot(df['step'], df['east_queue'] + df['west_queue'], label='East/West Axis', color='red')
    axes[0].set_title(f'Queue Length | {strategy.replace("_", " ").title()} (Imbalance: {imbalance}, Seed: {seed})')
    axes[0].set_ylabel('Number of Stopped Vehicles')
    axes[0].legend()
    axes[0].grid(True, linestyle='--', alpha=0.7)
    
    # 2. Delay (Directional Fairness)
    axes[1].plot(df['step'], df['north_delay'] + df['south_delay'], label='North/South Axis', color='blue')
    axes[1].plot(df['step'], df['east_delay'] + df['west_delay'], label='East/West Axis', color='red')
    axes[1].set_title(f'Accumulated Delay | {strategy.replace("_", " ").title()} (Imbalance: {imbalance}, Seed: {seed})')
    axes[1].set_ylabel('Total Waiting Time (seconds)')
    axes[1].legend()
    axes[1].grid(True, linestyle='--', alpha=0.7)
    
    # 3. Intersection Throughput
    axes[2].plot(df['step'], df['throughput'].cumsum(), label='Cumulative Throughput', color='green', linewidth=2)
    axes[2].set_title(f'Intersection Throughput | {strategy.replace("_", " ").title()} (Imbalance: {imbalance}, Seed: {seed})')
    axes[2].set_xlabel('Simulation Step')
    axes[2].set_ylabel('Total Vehicles Processed')
    axes[2].legend()
    axes[2].grid(True, linestyle='--', alpha=0.7)
    
    # Dynamic filename formatting
    output_file = f"results_{strategy}_imb{imbalance}_seed{seed}.png"
    plt.tight_layout()
    plt.savefig(output_file, dpi=300)
    plt.close()  # Closes the figure to prevent memory exhaustion in loops
    print(f"Analysis complete. Research graphs successfully saved to {output_file}")


# ---------------------------------------------------------------------- #
# Aggregate (cross-seed) comparison plots
# ---------------------------------------------------------------------- #
STRATEGY_STYLE = {
    "fixed_time": {"label": "Fixed-Time", "color": "#64748b"},
    "adaptive":   {"label": "Adaptive",   "color": "#2563eb"},
}
IMBALANCE_LABELS = {0.5: "50/50", 0.7: "70/30", 0.9: "90/10"}


def _imbalance_label(value: float) -> str:
    return IMBALANCE_LABELS.get(round(value, 2), str(value))


def _grouped_bars(ax, conditions: pd.DataFrame, metric: str, series: list, title: str, ylabel: str):
    """Draws grouped bars (x = imbalance) with 95% CI error bars.
    
    :param series: list of (strategy, metric_name, legend_label, color, hatch) tuples
    """
    imbalances = sorted(conditions["imbalance"].unique())
    x = np.arange(len(imbalances))
    width = 0.8 / len(series)

    for i, (strategy, metric_name, label, color, hatch) in enumerate(series):
        subset = (conditions[(conditions["strategy"] == strategy) & (conditions["metric"] == metric_name)]
                  .set_index("imbalance").reindex(imbalances))
        offset = (i - (len(series) - 1) / 2) * width
        ax.bar(x + offset, subset["mean"], width, yerr=subset["ci95"], capsize=4,
               label=label, color=color, hatch=hatch, edgecolor="white", linewidth=0.5)

    ax.set_xticks(x)
    ax.set_xticklabels([_imbalance_label(v) for v in imbalances])
    ax.set_xlabel("Traffic Split (NS / EW)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, axis="y", linestyle="--", alpha=0.6)
    ax.set_axisbelow(True)
    ax.legend(fontsize=8)


def _strategy_series(metric: str):
    return [(s, metric, style["label"], style["color"], None) for s, style in STRATEGY_STYLE.items()]


def generate_comparison_plots(conditions: pd.DataFrame, output_dir: str = "results") -> None:
    """Generates cross-seed comparison figures from the condition_summary table.
    
    Bars show the mean across seeds; error bars show the 95% confidence interval.
    Produces:
        comparison_efficiency.png - delay, waiting time, max queue, throughput
        comparison_fairness.png   - per-axis delay, per-axis max wait, EW/NS delay ratio
    """
    if conditions.empty:
        print("No condition summary data available for comparison plots.")
        return
    os.makedirs(output_dir, exist_ok=True)
    n_seeds = int(conditions["n"].max())

    # --- Figure 1: overall efficiency ---
    fig, axes = plt.subplots(2, 2, figsize=(12, 9))
    panels = [
        ("avg_delay", "Average Vehicle Delay", "Time loss per vehicle (s)"),
        ("avg_waiting_time", "Average Waiting Time", "Stopped time per vehicle (s)"),
        ("max_queue", "Maximum Queue Length", "Halting vehicles (all approaches)"),
        ("throughput", "Intersection Throughput", "Vehicles completed"),
    ]
    for ax, (metric, title, ylabel) in zip(axes.flat, panels):
        _grouped_bars(ax, conditions, metric, _strategy_series(metric), title, ylabel)
    fig.suptitle(f"Fixed-Time vs. Adaptive Control: Efficiency (mean ± 95% CI, n={n_seeds} seeds)", fontsize=13)
    fig.tight_layout()
    efficiency_file = os.path.join(output_dir, "comparison_efficiency.png")
    fig.savefig(efficiency_file, dpi=300)
    plt.close(fig)

    # --- Figure 2: directional fairness ---
    fig, axes = plt.subplots(1, 3, figsize=(17, 5.5))
    axis_series = lambda metric_suffix: [
        (s, f"{axis}_{metric_suffix}", f"{style['label']} - {axis.upper()}", style["color"], hatch)
        for s, style in STRATEGY_STYLE.items()
        for axis, hatch in (("ns", None), ("ew", "//"))
    ]
    _grouped_bars(axes[0], conditions, "ns_avg_delay", axis_series("avg_delay"),
                  "Average Delay by Direction", "Time loss per vehicle (s)")
    _grouped_bars(axes[1], conditions, "ns_max_wait", axis_series("max_wait"),
                  "Worst-Case Wait by Direction", "Max individual waiting time (s)")
    _grouped_bars(axes[2], conditions, "ew_ns_delay_ratio", _strategy_series("ew_ns_delay_ratio"),
                  "Minor/Major Delay Ratio (EW / NS)", "Ratio (1.0 = equal delay)")
    axes[2].axhline(1.0, color="black", linestyle=":", linewidth=1)
    fig.suptitle(f"Directional Fairness (mean ± 95% CI, n={n_seeds} seeds). NS is the major axis when split > 50/50",
                 fontsize=13)
    fig.tight_layout()
    fairness_file = os.path.join(output_dir, "comparison_fairness.png")
    fig.savefig(fairness_file, dpi=300)
    plt.close(fig)

    print(f"Comparison plots saved to {efficiency_file} and {fairness_file}")


if __name__ == "__main__":
    generate_research_plots(strategy="fixed", imbalance=0.9, seed=42)