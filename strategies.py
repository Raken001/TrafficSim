from abc import ABC, abstractmethod


class SignalStrategy(ABC):
    """Abstract base class for traffic signal control strategies.
    
    All signal strategies must implement determine_phase(), which receives
    current intersection telemetry and returns the phase index that should
    be active. The experiment loop in controller.py handles all TraCI calls;
    strategies never touch TraCI directly.
    
    Phase definitions (from baseline.net.xml, traffic light J0):
        0: NS green   (GGgrrrGGgrrr) - North/South through + left-yield
        1: NS yellow  (yyyrrryyyrrr) - North/South clearance
        2: EW green   (rrrGGgrrrGGg) - East/West through + left-yield
        3: EW yellow  (rrryyyrrryyy) - East/West clearance
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Returns the identifier for this strategy (used in database and logging)."""
        pass

    @abstractmethod
    def determine_phase(self, current_phase: int, time_in_phase: int,
                        queue_data: dict, delay_data: dict) -> int:
        """Determines which traffic light phase should be active.
        
        Args:
            current_phase: The currently active phase index (0-3).
            time_in_phase: Simulation steps elapsed in the current phase.
            queue_data: Dict mapping direction ('north','south','east','west')
                        to the number of halting vehicles on that incoming edge.
            delay_data: Dict mapping direction to cumulative waiting time (seconds)
                        on that incoming edge.
        
        Returns:
            The phase index (0-3) that should be active on the next step.
            Return current_phase to hold; return a different index to switch.
        """
        pass


class FixedTimeStrategy(SignalStrategy):
    """Fixed-time signal control with configurable phase durations.
    
    Cycles through phases in a fixed order:
        NS green -> NS yellow -> EW green -> EW yellow -> (repeat)
    
    Phase durations are predetermined and do not respond to traffic conditions.
    Default durations mirror the static tlLogic in baseline.net.xml (42/3/42/3).
    """

    def __init__(self, ns_green: int = 42, ew_green: int = 42, yellow: int = 3):
        """
        Args:
            ns_green: Green phase duration (steps) for the North/South axis.
            ew_green: Green phase duration (steps) for the East/West axis.
            yellow:   Yellow clearance duration (steps) for both axes.
        """
        self.phase_durations = {
            0: ns_green,  # NS green
            1: yellow,    # NS yellow
            2: ew_green,  # EW green
            3: yellow,    # EW yellow
        }

    @property
    def name(self) -> str:
        return "fixed_time"

    def determine_phase(self, current_phase: int, time_in_phase: int,
                        queue_data: dict, delay_data: dict) -> int:
        """Advances to the next phase once the current phase's duration is reached."""
        if time_in_phase >= self.phase_durations[current_phase]:
            return (current_phase + 1) % 4
        return current_phase


class AdaptiveStrategy(SignalStrategy):
    """Threshold-based adaptive signal control with anti-starvation guards.
    
    During a green phase the controller follows this state machine:
        1. HOLD for min_green steps (prevents rapid phase flickering)
        2. After min_green, CHECK if the opposing approach's combined queue
           exceeds queue_threshold → switch to yellow clearance
        3. At max_green, FORCE switch to yellow regardless of queues
           (anti-starvation: guarantees the minor approach gets served)
    
    Yellow phases always run for their full duration before advancing
    to the next green phase.
    
    All threshold values are parameterized so they can be updated with
    literature-backed values after a formal review.
    """

    def __init__(self, min_green: int = 15, max_green: int = 60,
                 yellow: int = 3, queue_threshold: int = 5):
        """
        Args:
            min_green:       Minimum green duration (steps) before a switch
                             can be triggered. Prevents rapid flickering.
            max_green:       Maximum green duration (steps) before a forced
                             switch. Prevents starvation of the minor approach.
            yellow:          Mandatory yellow clearance duration (steps).
            queue_threshold: Number of halting vehicles on the opposing
                             approach that triggers an early phase switch.
        """
        self.min_green = min_green
        self.max_green = max_green
        self.yellow = yellow
        self.queue_threshold = queue_threshold

    @property
    def name(self) -> str:
        return "adaptive"

    def determine_phase(self, current_phase: int, time_in_phase: int,
                        queue_data: dict, delay_data: dict) -> int:
        """Applies threshold-based actuation with min/max green guards.
        
        Green phase logic (phase 0 or 2):
            - time_in_phase < min_green  →  hold (no switching allowed)
            - time_in_phase >= max_green →  force switch to yellow
            - opposing queue >= threshold →  switch to yellow
            - otherwise                  →  extend current green
        
        Yellow phase logic (phase 1 or 3):
            - Always runs for full yellow duration, then advances.
        """
        # --- Yellow phases: mandatory full duration, then advance ---
        if current_phase in (1, 3):
            if time_in_phase >= self.yellow:
                return (current_phase + 1) % 4
            return current_phase

        # --- Green phases: min → threshold → max state machine ---
        # Determine the combined queue on the opposing (currently red) approach
        if current_phase == 0:  # NS green → check EW queue
            opposing_queue = queue_data.get("east", 0) + queue_data.get("west", 0)
        else:                   # EW green (phase 2) → check NS queue
            opposing_queue = queue_data.get("north", 0) + queue_data.get("south", 0)

        # 1. Minimum green: hold regardless of opposing demand
        if time_in_phase < self.min_green:
            return current_phase

        # 2. Maximum green: force switch to yellow (anti-starvation)
        if time_in_phase >= self.max_green:
            return current_phase + 1  # 0→1 or 2→3

        # 3. Threshold check: switch if opposing approach is congested
        if opposing_queue >= self.queue_threshold:
            return current_phase + 1  # 0→1 or 2→3

        # 4. No trigger: extend current green
        return current_phase
