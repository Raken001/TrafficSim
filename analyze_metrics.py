import pandas as pd
import matplotlib.pyplot as plt
import os
from datetime import datetime

def generate_research_plots(csv_file="baseline_metrics.csv", output_file=None):
    """Reads simulation metrics and generates visualizations for research analysis."""
    
    if output_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"baseline_results_{timestamp}.png"

    if not os.path.exists(csv_file):
        print(f"Error: '{csv_file}' not found. Please run controller.py first.")
        return

    # Ingest the generated dataset
    df = pd.read_csv(csv_file)
    
    # Initialize a 3-panel vertical figure
    fig, axes = plt.subplots(3, 1, figsize=(10, 15))
    
    # 1. Queue Length (Congestion Severity)
    axes[0].plot(df['step'], df['north_queue'] + df['south_queue'], label='North/South Axis', color='blue')
    axes[0].plot(df['step'], df['east_queue'] + df['west_queue'], label='East/West Axis', color='red')
    axes[0].set_title('Queue Length Over Time (Congestion Severity)')
    axes[0].set_ylabel('Number of Stopped Vehicles')
    axes[0].legend()
    axes[0].grid(True, linestyle='--', alpha=0.7)
    
    # 2. Delay (Directional Fairness)
    axes[1].plot(df['step'], df['north_delay'] + df['south_delay'], label='North/South Axis', color='blue')
    axes[1].plot(df['step'], df['east_delay'] + df['west_delay'], label='East/West Axis', color='red')
    axes[1].set_title('Accumulated Vehicle Delay (Directional Fairness)')
    axes[1].set_ylabel('Total Waiting Time (seconds)')
    axes[1].legend()
    axes[1].grid(True, linestyle='--', alpha=0.7)
    
    # 3. Intersection Throughput
    axes[2].plot(df['step'], df['throughput'].cumsum(), label='Cumulative Throughput', color='green', linewidth=2)
    axes[2].set_title('Total Intersection Throughput')
    axes[2].set_xlabel('Simulation Step')
    axes[2].set_ylabel('Total Vehicles Processed')
    axes[2].legend()
    axes[2].grid(True, linestyle='--', alpha=0.7)
    
    # Format and save
    plt.tight_layout()
    plt.savefig(output_file, dpi=300)
    print(f"Analysis complete. Research graphs successfully saved to {output_file}")

if __name__ == "__main__":
    generate_research_plots()