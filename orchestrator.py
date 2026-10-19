from datetime import datetime

from controller import TrafficExperiment
from strategies import FixedTimeStrategy, AdaptiveStrategy
from analyze_metrics import generate_research_plots
from process_data import ResultsProcessor

PLOT_INDIVIDUAL_RUNS = True

def run_experiments():
    imbalances = [0.5, 0.7, 0.9]
    seeds = [42, 105, 256, 789, 1024, 1337, 2048, 9999]
    strategies = [FixedTimeStrategy(), AdaptiveStrategy()]

    # All runs in this orchestrator invocation share a single batch_id.
    # This groups them in the database and allows process_data.py to
    # isolate or aggregate across historical batches.
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"=== Orchestrator batch: {batch_id} ===\n")

    for imbalance in imbalances:
        for seed in seeds:
            for strategy in strategies:
                print(f"--- Starting: {strategy.name} | Imbalance: {imbalance} | Seed: {seed} ---")
                
                experiment = TrafficExperiment("baseline.net.xml", "traffic.rou.xml")
                experiment.run(strategy=strategy, imbalance=imbalance, seed=seed, batch_id=batch_id)
                
                if PLOT_INDIVIDUAL_RUNS:
                    generate_research_plots(strategy.name, imbalance, seed, batch_id=batch_id)

    print("\n--- All simulations complete. Running data processing pipeline ---")
    processor = ResultsProcessor(batch_id=batch_id)
    processor.run(make_plots=True)

if __name__ == "__main__":
    run_experiments()