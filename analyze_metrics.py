import pandas as pd
import matplotlib.pyplot as plt
import sqlite3
import os

def generate_research_plots(strategy: str = "fixed", imbalance: float = 0.5, seed: int = 42, db_file: str = "research_data.db"):
    """Reads simulation metrics from SQLite database and generates visualizations for research analysis."""
    
    if not os.path.exists(db_file):
        print(f"Error: Database file '{db_file}' not found. Please run controller.py first.")
        return

    # Query the database for this specific run
    conn = sqlite3.connect(db_file)
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

if __name__ == "__main__":
    generate_research_plots(strategy="fixed", imbalance=0.9, seed=42)