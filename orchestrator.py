from controller import TrafficExperiment
from analyze_metrics import generate_research_plots

def run_experiments():
    imbalances = [0.5, 0.7, 0.9]
    seeds = [42, 105]  
    strategies = ["fixed_time"] 
    
    for imbalance in imbalances:
        for seed in seeds:
            for strategy in strategies:
                print(f"--- Starting: {strategy} | Imbalance: {imbalance} | Seed: {seed} ---")
                
                experiment = TrafficExperiment("baseline.net.xml", "traffic.rou.xml")
                experiment.run(strategy=strategy, imbalance=imbalance, seed=seed)
                
                # Automatically render the graph for this specific run
                generate_research_plots(strategy, imbalance, seed)

if __name__ == "__main__":
    run_experiments()