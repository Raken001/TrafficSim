import traci
import time
import pandas as pd
import sqlite3

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

    def generate_route_file(self, total_probability: float = 0.2, imbalance_ratio: float = 0.5) -> None:
        """Dynamically builds the traffic.rou.xml file based on imbalance ratio."""
        ns_prob = total_probability * imbalance_ratio
        ew_prob = total_probability * (1.0 - imbalance_ratio)
        
        xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<routes>
    <vType id="standard_car" length="5.0" maxSpeed="15.0" accel="2.6" decel="4.5" sigma="0.5"/>
    <route id="west_to_east" edges="-E2 E0"/>
    <route id="east_to_west" edges="-E0 E2"/>
    <route id="north_to_south" edges="-E1 E3"/>
    <route id="south_to_north" edges="-E3 E1"/>
    
    <flow id="flow_WE" type="standard_car" route="west_to_east" begin="0" end="500" probability="{ew_prob:.3f}"/>
    <flow id="flow_EW" type="standard_car" route="east_to_west" begin="0" end="500" probability="{ew_prob:.3f}"/>
    <flow id="flow_NS" type="standard_car" route="north_to_south" begin="0" end="500" probability="{ns_prob:.3f}"/>
    <flow id="flow_SN" type="standard_car" route="south_to_north" begin="0" end="500" probability="{ns_prob:.3f}"/>
</routes>"""

        with open(self.route_file, 'w') as f:
            f.write(xml_content)
        print(f"Generated {self.route_file} (NS: {ns_prob:.3f}, EW: {ew_prob:.3f})")

    def start_sim(self, seed: int = 42) -> None:
        """Initializes and launches the SUMO TraCI GUI server session."""
        # Note: Replace "sumo-gui" with "sumo" for headless execution
        traci.start([
            "sumo-gui", 
            "-n", self.network_file, 
            "-r", self.route_file, 
            "--seed", str(seed),
            "--quit-on-end"
        ])

    def run(self, strategy: str = "fixed", total_steps: int = 500, total_prob: float = 0.2, imbalance: float = 0.5, seed: int = 42) -> None:
        """Executes the simulation loop for a specified number of steps and collect metrics."""
        self.generate_route_file(total_prob, imbalance)
        self.start_sim(seed)
        step = 0
        
        # Map directional identifiers to their corresponding incoming edge IDs
        incoming_edges = {
            "west": "-E2",
            "east": "-E0",
            "north": "-E1",
            "south": "-E3"
        }
        
        try: 
            while step < total_steps:
                traci.simulationStep()
                
                step_metrics = {"step": step}
                
                # 1 & 2. Measure queue length and waiting time (delay) per direction
                for direction, edge_id in incoming_edges.items():
                    step_metrics[f"{direction}_queue"] = traci.edge.getLastStepHaltingNumber(edge_id)
                    step_metrics[f"{direction}_delay"] = traci.edge.getWaitingTime(edge_id)
                
                # 3. Measure total intersection throughput for the current step
                step_metrics["throughput"] = traci.simulation.getArrivedNumber()
                
                self.metrics.append(step_metrics)

                #time.sleep(0.05)  # Pace GUI visualization
                step += 1
        except Exception as e:
            print(f"Simulation error: {e}")
        finally:
            try:
                traci.close()
            except traci.exceptions.FatalTraCIError:
                print("TraCI server was already closed.")
            except Exception:
                pass
            
            # Pass the metadata to the database export
            self.export_data(strategy, imbalance, seed)

    def export_data(self, strategy: str, imbalance: float, seed: int) -> None:
        """Appends recorded simulation metrics and metadata into SQLite database (idempotently)."""
        if not self.metrics:
            print("No metrics collected to export.")
            return

        df = pd.DataFrame(self.metrics)
        df['strategy'] = strategy
        df['imbalance'] = imbalance
        df['seed'] = seed
        
        conn = sqlite3.connect("research_data.db")
        cursor = conn.cursor()
        
        # Make the insertion idempotent by clearing out old data for this specific run
        try:
            cursor.execute(
                "DELETE FROM simulation_metrics WHERE strategy=? AND imbalance=? AND seed=?", 
                (strategy, imbalance, seed)
            )
            conn.commit()
        except sqlite3.OperationalError:
            # Safely ignore the error if this is the very first run and the table does not exist yet
            pass
            
        # Append the fresh simulation data to SQLite database
        df.to_sql("simulation_metrics", conn, if_exists="append", index=False)
        conn.close()
        print("Experiment complete. Metrics exported to SQLite database (research_data.db).")

if __name__ == "__main__":
    experiment = TrafficExperiment("baseline.net.xml", "traffic.rou.xml")
    experiment.run(strategy="fixed", total_steps=500, total_prob=0.4, imbalance=0.7, seed=42)