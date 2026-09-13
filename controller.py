import traci
import time
import pandas as pd

class TrafficExperiment:
    """Manages a SUMO traffic simulation experiment via TraCI.
    
    Handles starting the SUMO server, collecting step-by-step edge telemetry 
    (queue lengths), and exporting metrics to CSV.
    """
    
    def __init__(self, network_file: str, route_file: str):
        """
        :param network_file: Path to SUMO network configuration (.net.xml)
        :param route_file: Path to SUMO route definition file (.rou.xml)
        """
        self.network_file = network_file
        self.route_file = route_file
        self.metrics = []

    def start_sim(self) -> None:
        """Initializes and launches the SUMO TraCI GUI server session."""
        # Note: Replace "sumo-gui" with "sumo" for headless execution
        traci.start([
            "sumo-gui", 
            "-n", self.network_file, 
            "-r", self.route_file, 
            "--quit-on-end"
        ])

    def run(self, total_steps: int = 500) -> None:
        """Executes the simulation loop for a specified number of steps."""
        self.start_sim()
        step = 0
        
        try: 
            while step < total_steps:
                traci.simulationStep()
                
                # Measure halted vehicles (queue length) on incoming approaches
                halted_west = traci.edge.getLastStepHaltingNumber("-E2")   # West approach
                halted_north = traci.edge.getLastStepHaltingNumber("-E1")  # North approach
                
                self.metrics.append({
                    "step": step,
                    "west_queue_length": halted_west,
                    "north_queue_length": halted_north
                })
                
                time.sleep(0.05)  # Pace GUI visualization
                step += 1
        except Exception as e:
            print(f"Simulation ended due to error {step}: {e}")
        finally:
            try:
                traci.close()
            except traci.exceptions.FatalTraCIError:
                print("TraCI server was already closed.")
            
            self.export_data()

    def export_data(self) -> None:
        """Saves recorded simulation metrics into baseline_metrics.csv."""
        if not self.metrics:
            print("No metrics collected to export.")
            return

        df = pd.DataFrame(self.metrics)
        df.to_csv("baseline_metrics.csv", index=False)
        print("Experiment complete. Metrics exported to baseline_metrics.csv")

if __name__ == "__main__":
    experiment = TrafficExperiment("baseline.net.xml", "traffic.rou.xml")
    experiment.run()
