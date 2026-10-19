import traci
import time
import os
import xml.etree.ElementTree as ET
from datetime import datetime
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

    def generate_route_file(self, total_steps: int = 600, total_probability: float = 0.2, imbalance_ratio: float = 0.5) -> None:
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
    
    <flow id="flow_WE" type="standard_car" route="west_to_east" begin="0" end="{total_steps}" probability="{ew_prob:.3f}"/>
    <flow id="flow_EW" type="standard_car" route="east_to_west" begin="0" end="{total_steps}" probability="{ew_prob:.3f}"/>
    <flow id="flow_NS" type="standard_car" route="north_to_south" begin="0" end="{total_steps}" probability="{ns_prob:.3f}"/>
    <flow id="flow_SN" type="standard_car" route="south_to_north" begin="0" end="{total_steps}" probability="{ns_prob:.3f}"/>
</routes>"""

        with open(self.route_file, 'w') as f:
            f.write(xml_content)
        print(f"Generated {self.route_file} (NS: {ns_prob:.3f}, EW: {ew_prob:.3f})")

    def start_sim(self, seed: int = 42, tripinfo_file: str = None, use_gui: bool = False) -> None:
        """Initializes and launches the SUMO TraCI server session.
        
        :param seed: Random seed passed to SUMO for reproducible vehicle arrivals.
        :param tripinfo_file: If given, SUMO writes per-vehicle trip statistics
                              (waitingTime, timeLoss, etc.) to this XML file.
        :param use_gui: If True, uses sumo-gui; otherwise uses headless sumo.
        """
        sumo_binary = "sumo-gui" if use_gui else "sumo"
        sumo_cmd = [
            sumo_binary, 
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

    def run(self, strategy: SignalStrategy, total_steps: int = 600, warmup_steps: int = 100, total_prob: float = 0.2, imbalance: float = 0.5, seed: int = 42, use_gui: bool = False, batch_id: str = None) -> None:
        """Executes the simulation loop for a specified number of steps and collect metrics.
        
        :param warmup_steps: Number of initial steps to run without recording metrics,
                             allowing the network to fill with a steady state of traffic.
        :param use_gui: Whether to run with the GUI and sleep delay.
        """
        self.generate_route_file(total_steps, total_prob, imbalance)
        
        # Unique per-run file so parallel runs (Progress Report 3) never collide
        os.makedirs("output", exist_ok=True)
        tripinfo_file = os.path.join("output", f"tripinfo_{strategy.name}_imb{imbalance}_seed{seed}.xml")
        self.start_sim(seed, tripinfo_file, use_gui)
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
                if use_gui:
                    time.sleep(0.05)
                
                # --- Strategy-driven phase control ---
                # We do this even during warmup so the traffic light behaves normally
                queue_data = {}
                delay_data = {}
                
                for direction, edge_id in incoming_edges.items():
                    queue = traci.edge.getLastStepHaltingNumber(edge_id)
                    delay = traci.edge.getWaitingTime(edge_id)
                    queue_data[direction] = queue
                    delay_data[direction] = delay
                
                time_in_phase += 1
                new_phase = strategy.determine_phase(
                    current_phase, time_in_phase, queue_data, delay_data
                )
                
                if new_phase != current_phase:
                    current_phase = new_phase
                    time_in_phase = 0
                    traci.trafficlight.setPhase(tls_id, current_phase)
                    traci.trafficlight.setPhaseDuration(tls_id, 99999)

                # --- Data Collection (Post-Warmup Only) ---
                if step >= warmup_steps:
                    step_metrics = {"step": step}
                    for direction in incoming_edges.keys():
                        step_metrics[f"{direction}_queue"] = queue_data[direction]
                        step_metrics[f"{direction}_delay"] = delay_data[direction]
                    
                    # Measure total intersection throughput for the current step
                    step_metrics["throughput"] = traci.simulation.getArrivedNumber()
                    self.metrics.append(step_metrics)

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
            
            # Only export to database when run as part of an orchestrated research batch.
            # Manual runs from controller.py __main__ (batch_id=None) skip export entirely.
            if batch_id:
                run_at = datetime.now().isoformat()
                self.export_data(strategy.name, imbalance, seed, batch_id, run_at)
                # SUMO only finishes writing tripinfo once TraCI is closed, so this must come after close()
                self.export_trip_data(tripinfo_file, strategy.name, imbalance, seed, batch_id, run_at, warmup_time=float(warmup_steps))
            else:
                print("Manual run (no batch_id). Skipping database export.")

    def export_data(self, strategy: str, imbalance: float, seed: int, batch_id: str, run_at: str) -> None:
        """Appends recorded simulation metrics into SQLite database with batch tracking.
        
        Each orchestrator run produces a unique batch_id, so no data is ever deleted.
        Historical batches accumulate for cross-batch statistical analysis.
        """
        if not self.metrics:
            print("No metrics collected to export.")
            return

        df = pd.DataFrame(self.metrics)
        df['strategy'] = strategy
        df['imbalance'] = imbalance
        df['seed'] = seed
        df['batch_id'] = batch_id
        df['run_at'] = run_at
        
        conn = sqlite3.connect("research_data.db")
        df.to_sql("simulation_metrics", conn, if_exists="append", index=False)
        # Performance indexes for filtering by batch or by run
        conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_batch ON simulation_metrics(batch_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_run ON simulation_metrics(strategy, imbalance, seed)")
        conn.close()
        print(f"Metrics exported to research_data.db (batch: {batch_id}).")

    def export_trip_data(self, tripinfo_file: str, strategy: str, imbalance: float, seed: int, batch_id: str, run_at: str, warmup_time: float = 100.0) -> None:
        """Parses SUMO's per-vehicle tripinfo XML into the vehicle_trips table with batch tracking.
        
        Each row is one vehicle. Vehicles still in the network when the simulation
        ended are kept and flagged with finished=0 (SUMO writes arrival=-1 for them).
        Vehicles that departed during the warmup period are excluded from the dataset.
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
            depart = float(trip.get("depart"))
            if depart < warmup_time:
                continue  # Skip vehicles that departed during warmup
                
            vehicle_id = trip.get("id")
            flow_id = vehicle_id.split(".")[0]
            origin, axis = FLOW_DIRECTIONS.get(flow_id, ("unknown", "unknown"))
            arrival = float(trip.get("arrival"))
            rows.append({
                "vehicle_id": vehicle_id,
                "origin": origin,
                "axis": axis,
                "depart": depart,
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
        df['batch_id'] = batch_id
        df['run_at'] = run_at

        conn = sqlite3.connect("research_data.db")
        df.to_sql("vehicle_trips", conn, if_exists="append", index=False)
        # Performance indexes for filtering by batch or by run
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trips_batch ON vehicle_trips(batch_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trips_run ON vehicle_trips(strategy, imbalance, seed)")
        conn.close()

        os.remove(tripinfo_file)
        print(f"Exported {len(df)} vehicle trips ({int(df['finished'].sum())} finished) to vehicle_trips (batch: {batch_id}).")

if __name__ == "__main__":
    experiment = TrafficExperiment("baseline.net.xml", "traffic.rou.xml")
    experiment.run(strategy=AdaptiveStrategy(), total_steps=500, total_prob=0.4, imbalance=0.9, seed=42, use_gui=True)