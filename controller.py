import traci
import time
import os
import xml.etree.ElementTree as ET
import pandas as pd
import sqlite3
from strategies import AdaptiveStrategy, SignalStrategy, FixedTimeStrategy

# Maps the flow ID prefix of each vehicle (e.g. "flow_NS.12") to its origin
# approach and signal axis. NS is the major axis whenever imbalance > 0.5.
FLOW_DIRECTIONS = {
    "flow_NS": ("north", "NS"),
    "flow_SN": ("south", "NS"),
    "flow_WE": ("west", "EW"),
    "flow_EW": ("east", "EW"),
}

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

    def start_sim(self, seed: int = 42, tripinfo_file: str = None) -> None:
        """Initializes and launches the SUMO TraCI GUI server session.
        
        :param seed: Random seed passed to SUMO for reproducible vehicle arrivals.
        :param tripinfo_file: If given, SUMO writes per-vehicle trip statistics
                              (waitingTime, timeLoss, etc.) to this XML file.
        """
        # Note: Replace "sumo-gui" with "sumo" for headless execution
        sumo_cmd = [
            "sumo", 
            "-n", self.network_file, 
            "-r", self.route_file, 
            "--seed", str(seed),
            "--quit-on-end"
        ]
        if tripinfo_file:
            # write-unfinished also records vehicles still in the network when the
            # simulation ends, so strategies that leave long queues are not
            # rewarded by having their slowest vehicles silently dropped.
            sumo_cmd += [
                "--tripinfo-output", tripinfo_file,
                "--tripinfo-output.write-unfinished",
            ]
        traci.start(sumo_cmd)

    def run(self, strategy: SignalStrategy, total_steps: int = 500, total_prob: float = 0.2, imbalance: float = 0.5, seed: int = 42) -> None:
        """Executes the simulation loop for a specified number of steps and collect metrics."""
        self.generate_route_file(total_prob, imbalance)
        
        # Unique per-run file so parallel runs (Progress Report 3) never collide
        os.makedirs("output", exist_ok=True)
        tripinfo_file = os.path.join("output", f"tripinfo_{strategy.name}_imb{imbalance}_seed{seed}.xml")
        self.start_sim(seed, tripinfo_file)
        step = 0
        
        # Map directional identifiers to their corresponding incoming edge IDs
        incoming_edges = {
            "west": "-E2",
            "east": "-E0",
            "north": "-E1",
            "south": "-E3"
        }
        
        # --- Traffic light control setup ---
        tls_id = "J0"
        current_phase = 0
        time_in_phase = 0
        
        # Take full Python control of the traffic light by setting an
        # indefinite phase duration so SUMO never auto-advances phases.
        traci.trafficlight.setPhase(tls_id, current_phase)
        traci.trafficlight.setPhaseDuration(tls_id, 99999)
        
        try: 
            while step < total_steps:
                traci.simulationStep()
                
                step_metrics = {"step": step}
                queue_data = {}
                delay_data = {}
                
                # 1 & 2. Measure queue length and waiting time (delay) per direction
                for direction, edge_id in incoming_edges.items():
                    queue = traci.edge.getLastStepHaltingNumber(edge_id)
                    delay = traci.edge.getWaitingTime(edge_id)
                    step_metrics[f"{direction}_queue"] = queue
                    step_metrics[f"{direction}_delay"] = delay
                    queue_data[direction] = queue
                    delay_data[direction] = delay
                
                # 3. Measure total intersection throughput for the current step
                step_metrics["throughput"] = traci.simulation.getArrivedNumber()
                
                self.metrics.append(step_metrics)
                
                # --- Strategy-driven phase control ---
                time_in_phase += 1
                new_phase = strategy.determine_phase(
                    current_phase, time_in_phase, queue_data, delay_data
                )
                
                if new_phase != current_phase:
                    current_phase = new_phase
                    time_in_phase = 0
                    traci.trafficlight.setPhase(tls_id, current_phase)
                    traci.trafficlight.setPhaseDuration(tls_id, 99999)

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
            self.export_data(strategy.name, imbalance, seed)
            # SUMO only finishes writing tripinfo once TraCI is closed, so this must come after close()
            self.export_trip_data(tripinfo_file, strategy.name, imbalance, seed)

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

    def export_trip_data(self, tripinfo_file: str, strategy: str, imbalance: float, seed: int) -> None:
        """Parses SUMO's per-vehicle tripinfo XML into the vehicle_trips table (idempotently).
        
        Each row is one vehicle. Vehicles still in the network when the simulation
        ended are kept and flagged with finished=0 (SUMO writes arrival=-1 for them).
        The temporary XML file is deleted after a successful import.
        """
        if not os.path.exists(tripinfo_file):
            print(f"No tripinfo file found at {tripinfo_file}; skipping trip export.")
            return

        try:
            root = ET.parse(tripinfo_file).getroot()
        except ET.ParseError as e:
            # Happens if SUMO was killed before it could finish writing the file
            print(f"Could not parse {tripinfo_file}: {e}")
            return

        rows = []
        for trip in root.iter("tripinfo"):
            vehicle_id = trip.get("id")
            flow_id = vehicle_id.split(".")[0]
            origin, axis = FLOW_DIRECTIONS.get(flow_id, ("unknown", "unknown"))
            arrival = float(trip.get("arrival"))
            rows.append({
                "vehicle_id": vehicle_id,
                "origin": origin,
                "axis": axis,
                "depart": float(trip.get("depart")),
                "arrival": arrival,
                "finished": int(arrival >= 0),
                "duration": float(trip.get("duration")),
                "waiting_time": float(trip.get("waitingTime")),
                "waiting_count": int(trip.get("waitingCount")),
                "time_loss": float(trip.get("timeLoss")),
                "depart_delay": float(trip.get("departDelay")),
            })

        if not rows:
            print("Tripinfo file contained no vehicles; skipping trip export.")
            return

        df = pd.DataFrame(rows)
        df['strategy'] = strategy
        df['imbalance'] = imbalance
        df['seed'] = seed

        conn = sqlite3.connect("research_data.db")
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM vehicle_trips WHERE strategy=? AND imbalance=? AND seed=?",
                (strategy, imbalance, seed)
            )
            conn.commit()
        except sqlite3.OperationalError:
            # Table does not exist yet on the very first run
            pass
        df.to_sql("vehicle_trips", conn, if_exists="append", index=False)
        conn.close()

        os.remove(tripinfo_file)
        print(f"Exported {len(df)} vehicle trips ({int(df['finished'].sum())} finished) to vehicle_trips.")

if __name__ == "__main__":
    experiment = TrafficExperiment("baseline.net.xml", "traffic.rou.xml")
    experiment.run(strategy=AdaptiveStrategy(), total_steps=500, total_prob=0.4, imbalance=0.9, seed=42)