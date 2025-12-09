#!/usr/bin/env python3
"""
Standalone script for Windows:
- Live load-cell reading & plotting (myDAQ)
- Motor jog control using keyboard arrow keys (keyboard library)
- ESC to quit, 'z' to re-zero (tare)
Save as loadcell_and_motor.py and run from a terminal.
"""

import threading
import time
import collections
import sys

import nidaqmx
from nidaqmx.constants import TerminalConfiguration, LineGrouping, FrequencyUnits, Level, AcquisitionType
import matplotlib.pyplot as plt
import keyboard  # Windows only (run script with appropriate privileges if needed)

# ------------------------
# USER CONFIG
# ------------------------
DEVICE = "myDAQ1"             # Change to "Dev1" if NI MAX shows that
AI_CHANNEL = "ai0"
TERMINAL_MODE = TerminalConfiguration.DIFF

V_FULL_SCALE = 10.0           # SGA full-scale (±10 V typical)
LOAD_FULL_SCALE = 1000.0      # load cell rating in lb (adjust if using different units)
CAL_FACTOR = 2.0              # calibration multiplicative factor (adjust with calibration procedure)

SAMPLE_INTERVAL = 0.05        # seconds between samples for plotting (20 Hz)
MOVING_AVG_SAMPLES = 5        # average to smooth jitter (increase if needed)

# Motor (counter & DO lines)
DEV = DEVICE
STEP_COUNTER = f"{DEV}/Ctr0"       # counter channel for pulses
DIR_LINE     = f"{DEV}/port0/line0"
EN_LINE      = f"{DEV}/port0/line7"

MICROSTEP = 8
STEPS_PER_REV = 200 * MICROSTEP * 15
print("Steps per rev =", STEPS_PER_REV)

F_FAST = 8000        # fast frequency (Hz)
F_SLOW = 1200        # slow frequency (Hz)
DUTY_CYCLE = 0.5

# ------------------------
# THREAD CONTROL
# ------------------------
stop_event = threading.Event()
tare_event = threading.Event()     # signal to re-zero (from keyboard 'z')
motor_lock = threading.Lock()      # protect motor operations if needed

# ------------------------
# GLOBALS (will be set at runtime)
# ------------------------
zero_offset = 0.0

# ------------------------
# Motor control thread
# ------------------------
def motor_thread_fn():
    """
    Runs in background. Creates its own NI tasks for DO and pulse generation,
    watches keyboard state and starts/stops a pulse task for stepping.
    """
    pulse_task = None

    # Create DO tasks for enable and direction (local to this thread)
    en_task = nidaqmx.Task()
    dir_task = nidaqmx.Task()
    try:
        en_task.do_channels.add_do_chan(EN_LINE, line_grouping=LineGrouping.CHAN_PER_LINE)
        dir_task.do_channels.add_do_chan(DIR_LINE, line_grouping=LineGrouping.CHAN_PER_LINE)

        # Enable driver (EN = LOW typically)
        en_task.write([False])

        print("[MOTOR] Motor thread started. Use arrow keys to jog. ESC to quit. 'z' to tare load cell.")

        while not stop_event.is_set():
            # Exit if ESC pressed
            if keyboard.is_pressed("esc"):
                stop_event.set()
                break

            # Determine requested action from arrow keys
            # Priority: down/up/right/left (you can adjust)
            if keyboard.is_pressed("down"):
                # CW fast
                dir_task.write([False])  # DIR = LOW -> CW (example)
                if pulse_task is None:
                    pulse_task = start_pulse_task(F_FAST)
            elif keyboard.is_pressed("up"):
                # CCW fast
                dir_task.write([True])
                if pulse_task is None:
                    pulse_task = start_pulse_task(F_FAST)
            elif keyboard.is_pressed("right"):
                # CCW slow
                dir_task.write([True])
                if pulse_task is None:
                    pulse_task = start_pulse_task(F_SLOW)
            elif keyboard.is_pressed("left"):
                # CW slow
                dir_task.write([False])
                if pulse_task is None:
                    pulse_task = start_pulse_task(F_SLOW)
            else:
                # No key pressed
                if pulse_task is not None:
                    try:
                        pulse_task.stop()
                        pulse_task.close()
                    except Exception as e:
                        print("[MOTOR] Error stopping pulse task:", e)
                    pulse_task = None

            # Check for tare key
            if keyboard.is_pressed("z"):
                # Debounce: only signal once per key press
                tare_event.set()
                # wait until key released to avoid many triggers
                while keyboard.is_pressed("z") and not stop_event.is_set():
                    time.sleep(0.05)

            time.sleep(0.01)  # small loop delay

    except Exception as e:
        print("[MOTOR] Exception in motor thread:", e)
        stop_event.set()

    finally:
        # Clean up
        if pulse_task is not None:
            try:
                pulse_task.stop()
                pulse_task.close()
            except Exception:
                pass
        # Disable driver (EN = HIGH to disable)
        try:
            en_task.write([True])
        except Exception:
            pass

        en_task.close()
        dir_task.close()
        print("[MOTOR] Motor thread exiting, driver disabled.")


def start_pulse_task(freq_hz):
    """
    Create and start a counter pulse task at freq_hz.
    Caller is responsible for stopping/closing returned Task.
    """
    t = nidaqmx.Task()
    # create pulse channel
    t.co_channels.add_co_pulse_chan_freq(
        counter=STEP_COUNTER,
        units=FrequencyUnits.HZ,
        freq=freq_hz,
        duty_cycle=DUTY_CYCLE,
        idle_state=Level.LOW
    )
    t.timing.cfg_implicit_timing(sample_mode=AcquisitionType.CONTINUOUS)
    t.start()
    return t

# ------------------------
# Main: load-cell read + plot (runs in main thread)
# ------------------------
def main():
    global zero_offset

    # moving average buffer
    mv_buffer = collections.deque(maxlen=MOVING_AVG_SAMPLES)

    # Create AI task in main thread
    ai_task = nidaqmx.Task()
    ai_channel_name = f"{DEVICE}/{AI_CHANNEL}"
    try:
        ai_task.ai_channels.add_ai_voltage_chan(
            ai_channel_name,
            terminal_config=TERMINAL_MODE,
            min_val=-10.0,
            max_val=10.0
        )
    except Exception as e:
        print("Error creating AI channel. Check device name and connection.", e)
        ai_task.close()
        return

    # Initial zero calibration (tare)
    print("Calibrating zero offset... ensure NO LOAD on the load cell.")
    zero_samples = []
    try:
        for _ in range(50):
            v = ai_task.read()
            zero_samples.append(v)
            time.sleep(0.01)
    except Exception as e:
        print("Error reading during zero calibration:", e)
    if zero_samples:
        zero_offset = sum(zero_samples) / len(zero_samples)
    else:
        zero_offset = 0.0
    print(f"Zero offset voltage = {zero_offset:.6f} V")

    # Start motor thread
    motor_thread = threading.Thread(target=motor_thread_fn, daemon=True)
    motor_thread.start()

    # Prepare plotting
    plt.ion()
    fig, ax = plt.subplots(figsize=(9, 5))
    line, = ax.plot([], [], lw=1.5)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Load (lb)")
    ax.set_title("Live Load Cell")
    ax.grid(True)

    times = []
    forces = []
    start_time = time.time()

    print("Starting data collection. Press ESC to stop. Press 'z' to re-zero (tare).")

    try:
        while not stop_event.is_set():
            # Read one or a few samples for averaging
            try:
                v = ai_task.read()
            except Exception as e:
                print("AI read error:", e)
                stop_event.set()
                break

            # apply moving average
            mv_buffer.append(v)
            v_avg = sum(mv_buffer) / len(mv_buffer)

            # If tare was requested by motor thread, compute a new zero_offset
            if tare_event.is_set():
                # take a few extra samples to compute fresh zero
                print("[MAIN] Re-zeroing (tare) ... keep load removed")
                samples = []
                for _ in range(50):
                    try:
                        samples.append(ai_task.read())
                    except Exception:
                        pass
                    time.sleep(0.005)
                if samples:
                    zero_offset = sum(samples) / len(samples)
                    print(f"[MAIN] New zero offset = {zero_offset:.6f} V")
                tare_event.clear()

            # convert to force
            force = ((v_avg - zero_offset) / V_FULL_SCALE) * LOAD_FULL_SCALE
            force *= CAL_FACTOR

            t = time.time() - start_time
            times.append(t)
            forces.append(force)

            # Update plot data and view
            line.set_xdata(times)
            line.set_ydata(forces)
            ax.relim()
            ax.autoscale_view()
            fig.canvas.draw()
            fig.canvas.flush_events()

            # Handle large data arrays: keep last N points to keep GUI responsive
            if len(times) > 5000:
                times = times[-2000:]
                forces = forces[-2000:]

            time.sleep(SAMPLE_INTERVAL)

    except KeyboardInterrupt:
        stop_event.set()
    finally:
        # cleanup
        stop_event.set()
        print("\nShutting down...")

        try:
            ai_task.close()
        except Exception:
            pass

        # Wait for motor thread to end
        motor_thread.join(timeout=2.0)
        print("All threads joined. Exiting.")

        # show final static plot
        plt.ioff()
        plt.figure(figsize=(9,5))
        plt.plot(times, forces, label="Load (lb)")
        plt.xlabel("Time (s)")
        plt.ylabel("Load (lb)")
        plt.title("Final Load Cell Data")
        plt.grid(True)
        plt.legend()
        plt.show()


if __name__ == "__main__":
    main()
