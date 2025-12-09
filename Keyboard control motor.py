import nidaqmx
from nidaqmx.constants import LineGrouping, FrequencyUnits, Level, AcquisitionType
import time
import keyboard   # <-- for arrow-key control

DEV = "myDAQ1"

STEP_COUNTER = f"{DEV}/Ctr0"          # hardware pulse output → PFI3 → SN74HC04 → TB6600 PUL+
DIR_LINE     = f"{DEV}/port0/line0"   # direction (3.3 V)
EN_LINE      = f"{DEV}/port0/line7"   # enable (3.3 V)

# ------------------------------
# USER SETTINGS
# ------------------------------
microstep = 8
steps_per_rev = 200 * microstep * 15
print("Steps per rev =", steps_per_rev)

f_target = 8000       # Hz (change as needed)
f_slow = 1200
duty_cycle = 0.5


# ------------------------------
# Enable driver
# ------------------------------
en_task = nidaqmx.Task()
en_task.do_channels.add_do_chan(EN_LINE, line_grouping=LineGrouping.CHAN_PER_LINE)
en_task.write([False])    # EN = LOW → driver enabled

# ------------------------------
# Direction control task
# ------------------------------
dir_task = nidaqmx.Task()
dir_task.do_channels.add_do_chan(DIR_LINE, line_grouping=LineGrouping.CHAN_PER_LINE)


# ------------------------------
# Function to start hardware pulse
# ------------------------------
def start_pulse(freq):
    t = nidaqmx.Task()
    t.co_channels.add_co_pulse_chan_freq(
        counter=STEP_COUNTER,
        units=FrequencyUnits.HZ,
        freq=freq,
        duty_cycle=duty_cycle,
        idle_state=Level.LOW
    )
    t.timing.cfg_implicit_timing(sample_mode=AcquisitionType.CONTINUOUS)
    t.start()
    return t


# ------------------------------
# MAIN CONTROL LOOP
# ------------------------------
pulse_task = None
print("\nMotor ready. Hold UP/DOWN to move. Press ESC to quit.\n")

try:
    while True:

        # EXIT
        if keyboard.is_pressed("esc"):
            break

        # MOVE CW fast (DOWN ARROW)
        if keyboard.is_pressed("down"):
            dir_task.write([False])   # DIR = LOW → CW
            if pulse_task is None:
                pulse_task = start_pulse(f_target)
        # MOVE CCW fast (UP ARROW)
        elif keyboard.is_pressed("up"):
            dir_task.write([True])    # DIR = HIGH → CCW
            if pulse_task is None:
                pulse_task = start_pulse(f_target)
         # MOVE CCW slow (Right ARROW)
        elif keyboard.is_pressed("right"):
            dir_task.write([True])    # DIR = HIGH → CCW
            if pulse_task is None:
                pulse_task = start_pulse(f_slow)
         # MOVE CW (LEFT ARROW)
        elif keyboard.is_pressed("left"):
            dir_task.write([False])    # DIR = LOW → CW
            if pulse_task is None:
                pulse_task = start_pulse(f_slow)
        else:
            # No key pressed → stop pulse
            if pulse_task is not None:
                pulse_task.stop()
                pulse_task.close()
                pulse_task = None

        time.sleep(0.01)  # small loop delay

finally:
    # Stop everything safely
    if pulse_task is not None:
        pulse_task.stop()
        pulse_task.close()

    en_task.write([True])   # EN = HIGH → disable driver
    en_task.close()
    dir_task.close()

    print("\nDriver disabled. Program finished.")


