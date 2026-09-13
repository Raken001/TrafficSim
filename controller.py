import traci
import time

def run_simulation():
    # Start the SUMO GUI and load the 4-way intersection network
    traci.start([
        "sumo-gui", 
        "-n", "baseline.net.xml", 
        "-r", "traffic.rou.xml"
    ])
    
    step = 0
    # Run simulation for 500 steps
    while step < 500:
        traci.simulationStep()
        time.sleep(0.05) # Slows down the GUI to watch the lights change
        step += 1
        
    traci.close()

if __name__ == "__main__":
    run_simulation()