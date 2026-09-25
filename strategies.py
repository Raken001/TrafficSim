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
