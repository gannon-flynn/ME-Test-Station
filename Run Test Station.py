import time
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter import filedialog


import nidaqmx
from nidaqmx.constants import (
    TerminalConfiguration,
    LineGrouping,
    FrequencyUnits,
    Level,
    AcquisitionType,
)

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import os



# ============================================
# CONFIGURATION CONSTANTS
# ============================================

DEVICE = "myDAQ1"
AI_CHANNEL = "ai0"
TERMINAL_MODE = TerminalConfiguration.DIFF

# Load cell scaling
V_FULL_SCALE = 10.0
LOAD_FULL_SCALE = 1000.0
CAL_FACTOR = 2.0
SAMPLE_INTERVAL = 0.1   # seconds

# Motor mechanical configuration
STEPPER_STEPS = 200
MICROSTEP = 8
GEAR_RATIO = 15
PULSES_PER_REV = STEPPER_STEPS * MICROSTEP * GEAR_RATIO  # 24000

# Pulley + screw conversion
PULLEY_RATIO = 34 / 24      # gear ratio correction
SCREW_PITCH_MM = 5.0
MM_TO_INCH = 1 / 25.4
TRAVEL_IN_PER_PULSE = (PULLEY_RATIO *
                       (SCREW_PITCH_MM * MM_TO_INCH) /
                       PULSES_PER_REV)

# Motor I/O
STEP_COUNTER = f"{DEVICE}/Ctr0"
DIR_LINE     = f"{DEVICE}/port0/line0"
EN_LINE      = f"{DEVICE}/port0/line7"

F_FAST = 8000.0
F_SLOW = 1200.0
DUTY   = 0.5



# ============================================
# MAIN APPLICATION CLASS
# ============================================

class TestMachineApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Test Machine GUI — Compression & Tension")

        self.export_folder = os.path.join(os.path.expanduser("~"), "Desktop")


        # DAQ state
        self.zero_offset = 0.0
        self.ai_task = None
        self.en_task = None
        self.dir_task = None
        self.pulse_task = None

        # Machine state variables
        self.current_freq = 0.0
        self.current_dir = 0
        self.pulse_accumulator = 0.0
        self.current_force = 0.0
        self.current_travel_in = 0.0
        self.last_update_time = None

        # Graph state
        self.fig = None
        self.ax = None
        self.line_force = None
        self.scatter_force = None
        self.canvas = None
        self.travel_data = []
        self.force_data = []
        self.graph_start_travel_offset = 0.0
        self.graph_start_force_offset = 0.0

        # Test recording state
        self.test_active = False
        self.export_csv = tk.BooleanVar(value=True)
        self.export_png = tk.BooleanVar(value=True)
        self.test_time_data = []
        self.test_force_data = []
        self.test_travel_data = []
        self.test_start_time = None


        # Axis settings
        self.axis_mode = 1
        self.xmin = 0.0
        self.xmax = 1.0
        self.ymin = 0.0
        self.ymax = 100.0

        # Default test mode = COMPRESSION
        self.test_mode_var = tk.StringVar(value="Compression")

        # Build UI + DAQ
        self._build_ui()
        self._setup_daq()
        self._create_graph()

        # Start update loop
        self.root.after(int(SAMPLE_INTERVAL * 1000), self.update_loop)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)



    # ============================================
    # BUILD USER INTERFACE
    # ============================================

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill="both", expand=True)

        # 2×2 grid for compact controls
        control_grid = ttk.Frame(main_frame)



        # -----------------------------------
        # STATUS PANELS: machine + graph
        # -----------------------------------
        status_container = ttk.Frame(main_frame)
        status_container.grid(row=0, column=0, padx=5, pady=5)

        # ------ Machine Status ------
        machine_frame = ttk.LabelFrame(status_container, text="Travel Since Startup")
        machine_frame.grid(row=0, column=0, padx=5, pady=5)

        self.machine_force_var = tk.StringVar(value="Force: 0.00 lb")
        self.machine_travel_var = tk.StringVar(value="Travel: 0.0000 in")

        ttk.Label(machine_frame, textvariable=self.machine_force_var).grid(row=0, column=0, sticky="w")
        ttk.Label(machine_frame, textvariable=self.machine_travel_var).grid(row=1, column=0, sticky="w")

        # Add re-zero load cell button
        ttk.Button(
            machine_frame, text="Re-zero Load Cell",
            command=self.rezero_load_cell
        ).grid(row=2, column=0, sticky="w", padx=5, pady=5)

        # ------ Graph Status ------
        graph_status_frame = ttk.LabelFrame(
            status_container,
            text="Current Readings",
            padding=(25, 20)   # ← make inside bigger
        )

        graph_status_frame.grid(row=0, column=1, padx=5, pady=5)

        self.graph_force_var = tk.StringVar(value="Force: 0.00 lb")
        self.graph_travel_var = tk.StringVar(value="Travel: 0.0000 in")

        ttk.Label(graph_status_frame, textvariable=self.graph_force_var, font=("Segoe UI", 14)).grid(row=0, column=0, sticky="w")
        ttk.Label(graph_status_frame, textvariable=self.graph_travel_var, font=("Segoe UI", 14)).grid(row=1, column=0, sticky="w")

    

        # -----------------------------------
        # MOTOR CONTROL — UP/DOWN
        # -----------------------------------
        motor_frame = ttk.LabelFrame(main_frame, text="Motor Control")
        motor_frame.grid(row=0, column=1, padx=5, pady=5)

        ttk.Button(motor_frame, text="Down Fast", width=15,
                   command=lambda: self.start_motor(-1, F_FAST)).grid(row=0, column=0)
        ttk.Button(motor_frame, text="Up Fast", width=15,
                   command=lambda: self.start_motor(+1, F_FAST)).grid(row=0, column=1)

        ttk.Button(motor_frame, text="Down Slow", width=15,
                   command=lambda: self.start_motor(-1, F_SLOW)).grid(row=1, column=0)
        ttk.Button(motor_frame, text="Up Slow", width=15,
                   command=lambda: self.start_motor(+1, F_SLOW)).grid(row=1, column=1)

        ttk.Button(motor_frame, text="Stop", width=32,
                   command=self.stop_motor).grid(row=2, column=0, columnspan=2, pady=4)

        # -----------------------------------
        # GRAPH CONTROL
        # -----------------------------------
        graph_ctrl_frame = ttk.LabelFrame(main_frame, text="Graph Control")
        graph_ctrl_frame.grid(row=1, column=1, padx=5, pady=5)

        ttk.Button(graph_ctrl_frame, text="Reset Graph", width=15,
                   command=self.reset_graph).grid(row=0, column=0)

        ttk.Button(graph_ctrl_frame, text="Graph Settings", width=15,
                   command=self.open_graph_settings).grid(row=0, column=1)

        ttk.Button(graph_ctrl_frame, text="Quit", width=32,
                   command=self.on_close).grid(row=1, column=0, columnspan=2, pady=4)

        # -----------------------------------
        # Data Collection FRAME
        # -----------------------------------
        testing_frame = ttk.LabelFrame(main_frame, text="Data Collection")
        testing_frame.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")


        # --- Test Type Selection (Compression / Tension) ---
        ttk.Label(testing_frame, text="Test Type:").grid(row=0, column=0, sticky="e", padx=5)
        ttk.OptionMenu(
            testing_frame,
            self.test_mode_var,
            "Compression",
            "Compression",
            "Tension"
        ).grid(row=0, column=1, sticky="w", padx=5, pady=5)


        # Start Test
        ttk.Button(testing_frame, text="Start Test",
                   width=20, command=self.start_test).grid(row=1, column=0, padx=5, pady=5)

        # End Test
        ttk.Button(testing_frame, text="End Test",
                   width=20, command=self.end_test).grid(row=1, column=1, padx=5, pady=5)
        
        # Export Results (CSV & PNG)
        ttk.Button(testing_frame, text="Export Results",
                    width=20, command=self.export_results).grid(row=1, column=2, padx=5, pady=5)
        
        # -----------------------------------
        # Export Folder Selection
        # -----------------------------------

        ttk.Label(testing_frame, text="Export Folder:").grid(row=7, column=1, sticky="e", padx=5)
        self.export_folder_var = tk.StringVar(value=self.export_folder)

        ttk.Label(testing_frame, textvariable=self.export_folder_var, width=20)\
            .grid(row=7, column=2, sticky="w", padx=5)

        ttk.Button(testing_frame, text="Change Folder", width=20,
                command=self.change_export_folder)\
            .grid(row=8, column=1, columnspan=2, pady=5)




        
        # --- FORCE AT TRAVEL SECTION ---
        ttk.Separator(testing_frame, orient="horizontal").grid(row=3, column=0, columnspan=3, sticky="ew", pady=5)

        ttk.Label(testing_frame, text="Force at Travel:").grid(row=4, column=0, columnspan=2, sticky="w", padx=5)

        ttk.Label(testing_frame, text="Travel (in):").grid(row=5, column=0, sticky="e", padx=5)
        self.fat_input = tk.Entry(testing_frame, width=10)
        self.fat_input.grid(row=5, column=1, sticky="w", padx=5)

        ttk.Button(testing_frame, text="Get Force",
           command=self.query_force_at_travel).grid(row=6, column=0, columnspan=2, pady=5)

        self.fat_result = tk.StringVar(value="Result: ---")
        ttk.Label(testing_frame, textvariable=self.fat_result).grid(row=7, column=0, columnspan=2, sticky="w", padx=5, pady=5)
    
        # --- STATUS LABEL ---
        self.test_status_var = tk.StringVar(value="Status: Test Not Running")
        ttk.Label(testing_frame, textvariable=self.test_status_var, font=("Segoe UI", 11, "bold")).grid(
            row=8, column=0, columnspan=2, sticky="w", padx=5, pady=(10, 5)
        )





    # ============================================
    # DAQ INITIALIZATION
    # ============================================

    def _setup_daq(self):
        """Initialize NI myDAQ analog input + digital outputs."""

        # ---- AI: load cell ----
        self.ai_task = nidaqmx.Task()
        self.ai_task.ai_channels.add_ai_voltage_chan(
            f"{DEVICE}/{AI_CHANNEL}",
            terminal_config=TERMINAL_MODE,
            min_val=-10.0,
            max_val=10.0
        )

        print("Calibrating zero offset…")
        zeros = [self.ai_task.read() for _ in range(50)]
        self.zero_offset = sum(zeros) / len(zeros)
        print(f"Zero offset = {self.zero_offset:.6f} V")

        # ---- Enable line (ACTIVE LOW) ----
        self.en_task = nidaqmx.Task()
        self.en_task.do_channels.add_do_chan(
            EN_LINE, line_grouping=LineGrouping.CHAN_PER_LINE
        )
        self.en_task.write([False])   # enable motor driver

        # ---- Direction line ----
        self.dir_task = nidaqmx.Task()
        self.dir_task.do_channels.add_do_chan(
            DIR_LINE, line_grouping=LineGrouping.CHAN_PER_LINE
        )

        self.pulse_task = None





    # ============================================
    # GRAPH CREATION
    # ============================================

    def _create_graph(self):
        self.fig = Figure(figsize=(6, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Travel (in)")
        self.ax.set_ylabel("Force (lb)")
        self.ax.grid(True)

        (self.line_force,) = self.ax.plot([], [], "b-", label="Force vs Travel")
        (self.scatter_force,) = self.ax.plot([], [], "ro", markersize=3)
        self.ax.legend(loc="best")

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.root)
        self.canvas.get_tk_widget().pack(side="right", fill="both", expand=True)

        self.fig.subplots_adjust(bottom=0.3)
        self.fig.tight_layout()
        self.canvas.draw()


        self.reset_graph()



    # ============================================
    # RESET GRAPH – force remains correct
    # ============================================

    def reset_graph(self):
        """Reset travel to zero but KEEP force equal to absolute force."""
        self.travel_data = []
        self.force_data = []

        # Travel resets to zero
        self.graph_start_travel_offset = self.current_travel_in

        # Force stays absolute — do NOT zero it
        self.graph_start_force_offset = 0.0

        force_sign = -1 if self.test_mode_var.get() == "Compression" else 1
        abs_force = force_sign * self.current_force

        # Update graph status labels
        self.graph_force_var.set(f"Force: {abs_force:0.2f} lb")
        self.graph_travel_var.set("Travel: 0.0000 in")

        # Clear the plot
        self.line_force.set_data([], [])
        self.scatter_force.set_data([], [])
        self.ax.relim()
        self.ax.autoscale_view()
        self.canvas.draw_idle()


    # ============================================
    # GRAPH SETTINGS WINDOW
    # ============================================

    def open_graph_settings(self):
        """Popup window to configure axis behavior and limits."""
        win = tk.Toplevel(self.root)
        win.title("Graph Settings")
        win.grab_set()  # modal

        mode_var = tk.IntVar(value=self.axis_mode)

        frm = ttk.Frame(win, padding=10)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Axis Mode:").grid(row=0, column=0, sticky="w")

        modes = [
            (1, "1: Autoscale X & Y"),
            (2, "2: Fixed X & Y"),
            (3, "3: Autoscale X, Fixed Y"),
            (4, "4: Autoscale Y, Fixed X"),
        ]
        for i, (val, text) in enumerate(modes, start=1):
            ttk.Radiobutton(
                frm, text=text, variable=mode_var, value=val
            ).grid(row=i, column=0, columnspan=2, sticky="w", pady=2)

        # X/Y limit entries
        ttk.Label(frm, text="X min:").grid(row=5, column=0, sticky="e")
        x_min_entry = ttk.Entry(frm)
        x_min_entry.grid(row=5, column=1, padx=5, pady=2)
        x_min_entry.insert(0, str(self.xmin))

        ttk.Label(frm, text="X max:").grid(row=6, column=0, sticky="e")
        x_max_entry = ttk.Entry(frm)
        x_max_entry.grid(row=6, column=1, padx=5, pady=2)
        x_max_entry.insert(0, str(self.xmax))

        ttk.Label(frm, text="Y min:").grid(row=7, column=0, sticky="e")
        y_min_entry = ttk.Entry(frm)
        y_min_entry.grid(row=7, column=1, padx=5, pady=2)
        y_min_entry.insert(0, str(self.ymin))

        ttk.Label(frm, text="Y max:").grid(row=8, column=0, sticky="e")
        y_max_entry = ttk.Entry(frm)
        y_max_entry.grid(row=8, column=1, padx=5, pady=2)
        y_max_entry.insert(0, str(self.ymax))

        def apply():
            try:
                self.axis_mode = mode_var.get()
                self.xmin = float(x_min_entry.get())
                self.xmax = float(x_max_entry.get())
                self.ymin = float(y_min_entry.get())
                self.ymax = float(y_max_entry.get())
                win.destroy()
            except ValueError:
                messagebox.showerror("Invalid input", "Please enter numeric limits.")

        ttk.Button(frm, text="OK", command=apply).grid(row=9, column=0, pady=10)
        ttk.Button(frm, text="Cancel", command=win.destroy).grid(row=9, column=1, pady=10)


    # ============================================
    # START TEST
    # ============================================
    def start_test(self):
        """Begin a new test: reset graph & begin recording."""
        self.reset_graph()

        # Reset test data
        self.test_time_data = []
        self.test_force_data = []
        self.test_travel_data = []

        self.test_start_time = time.time()
        self.test_active = True

        self.test_status_var.set("Status: Test Running")



    # ============================================
    # FORCE AT TRAVEL QUERY
    # ============================================
    def query_force_at_travel(self):
        """Return the average force of the 4 closest travel points to the requested travel."""
        try:
            target = float(self.fat_input.get())
        except ValueError:
            self.fat_result.set("Result: Invalid travel value")
            return

        if not self.travel_data:
            self.fat_result.set("Result: No data")
            return

        # --- Find distances of all points from target ---
        distances = [(abs(travel - target), idx) for idx, travel in enumerate(self.travel_data)]

        # --- Sort by closest distance ---
        distances.sort(key=lambda x: x[0])

        # --- Get indices of the 4 closest points ---
        closest_indices = [idx for (_, idx) in distances[:4]]

        # --- Compute average force for these points ---
        closest_forces = [self.force_data[i] for i in closest_indices]
        avg_force = sum(closest_forces) / len(closest_forces)

        # Update label
        self.fat_result.set(f"Result: {avg_force:.2f} lb")




    # ============================================
    # RE-ZERO LOAD CELL
    # ============================================

    def rezero_load_cell(self):
        """Re-zero the load cell and immediately update displays."""
        try:
            zeros = [self.ai_task.read() for _ in range(50)]
            self.zero_offset = sum(zeros) / len(zeros)
        except Exception as e:
            messagebox.showerror("DAQ Error", f"Could not re-zero load cell:\n{e}")
            return

        # Reset force
        self.current_force = 0.0

        # Update absolute force display
        force_sign = -1 if self.test_mode_var.get() == "Compression" else 1
        self.machine_force_var.set(f"Force: {0.00 * force_sign:0.2f} lb")

        # Update relative graph force
        self.graph_start_force_offset = 0.0
        self.graph_force_var.set("Force: 0.00 lb")

        messagebox.showinfo("Load Cell Zeroed", "Load cell has been re-zeroed.")


    # ============================================
    # END TEST
    # ============================================
    def end_test(self):
        """Stop test and motor."""

        self.stop_motor()


        if not self.test_active:
            messagebox.showwarning("No Active Test", "No test is currently running.")
            return

        self.test_active = False

        self.test_status_var.set("Status: Test Not Running")




    # ============================================
    # MOTOR CONTROL
    # ============================================

    def start_motor(self, direction: int, freq: float):
        """direction: +1 = UP (CCW), -1 = DOWN (CW)"""
        self.current_dir = direction
        self.current_freq = freq

        # Set direction line
        self.dir_task.write([True if direction > 0 else False])

        if self.pulse_task is None:
            self.pulse_task = nidaqmx.Task()
            self.pulse_task.co_channels.add_co_pulse_chan_freq(
                counter=STEP_COUNTER,
                units=FrequencyUnits.HZ,
                freq=freq,
                duty_cycle=DUTY,
                idle_state=Level.LOW
            )
            self.pulse_task.timing.cfg_implicit_timing(AcquisitionType.CONTINUOUS)
            self.pulse_task.start()

    def stop_motor(self):
        self.current_freq = 0.0
        self.current_dir = 0

        if self.pulse_task:
            try:
                self.pulse_task.stop()
                self.pulse_task.close()
            except:
                pass
            self.pulse_task = None



    # ============================================
    # MAIN UPDATE LOOP
    # ============================================

    def update_loop(self):
        now = time.time()
        dt = SAMPLE_INTERVAL if self.last_update_time is None else (now - self.last_update_time)
        self.last_update_time = now

        # Read load cell
        try:
            v = self.ai_task.read()
        except Exception:
            self.on_close()
            return

        self.current_force = ((v - self.zero_offset) / V_FULL_SCALE) * LOAD_FULL_SCALE
        self.current_force *= CAL_FACTOR

        # Travel update
        if self.current_freq > 0 and self.current_dir != 0:
            self.pulse_accumulator += self.current_dir * self.current_freq * dt

        self.current_travel_in = self.pulse_accumulator * TRAVEL_IN_PER_PULSE

        # Mode sign flip
        if self.test_mode_var.get() == "Compression":
            force_sign = -1
            travel_sign = -1
        else:
            force_sign = +1
            travel_sign = +1

        # Absolute readings
        abs_force = force_sign * self.current_force
        abs_travel = travel_sign * self.current_travel_in
        self.machine_force_var.set(f"Force: {abs_force:0.2f} lb")
        self.machine_travel_var.set(f"Travel: {abs_travel:0.4f} in")

        # Relative readings
        rel_force = force_sign * (self.current_force - self.graph_start_force_offset)
        rel_travel = travel_sign * (self.current_travel_in - self.graph_start_travel_offset)
        self.graph_force_var.set(f"Force: {rel_force:0.2f} lb")
        self.graph_travel_var.set(f"Travel: {rel_travel:0.4f} in")

        # -----------------------------------
        # Test recording
        # -----------------------------------
        if self.test_active:
            elapsed = now - self.test_start_time
            self.test_time_data.append(elapsed)
            self.test_force_data.append(rel_force)
            self.test_travel_data.append(rel_travel)


        # Graph update
        self.travel_data.append(rel_travel)
        self.force_data.append(rel_force)

        self.line_force.set_data(self.travel_data, self.force_data)
        self.scatter_force.set_data(self.travel_data, self.force_data)

        # Axis behavior
        if self.axis_mode == 1:
            self.ax.relim()
            self.ax.autoscale_view()
        elif self.axis_mode == 2:
            self.ax.set_xlim(self.xmin, self.xmax)
            self.ax.set_ylim(self.ymin, self.ymax)
        elif self.axis_mode == 3:
            self.ax.set_ylim(self.ymin, self.ymax)
            self.ax.set_xlim(min(self.travel_data), max(self.travel_data))
        elif self.axis_mode == 4:
            self.ax.set_xlim(self.xmin, self.xmax)
            self.ax.set_ylim(min(self.force_data), max(self.force_data))

        self.canvas.draw_idle()

        # Repeat loop
        self.root.after(int(SAMPLE_INTERVAL * 1000), self.update_loop)

    # -----------------------------------
    # Export Results
    # -----------------------------------
    
    def export_results(self):
        """Export CSV and PNG only when the user presses the button."""

        if not self.test_time_data:
            messagebox.showwarning("No Data", "No test data available to export.")
            return

        # --- Export CSV ---
        if self.export_csv.get():
            filename = f"test_data_{int(time.time())}.csv"
            full_path = os.path.join(self.export_folder, filename)
            try:
                with open(full_path, "w") as f:
                    f.write("time,travel,force\n")
                    for t, tr, fr in zip(self.test_time_data, self.test_travel_data, self.test_force_data):
                        f.write(f"{t},{tr},{fr}\n")
            except Exception as e:
                messagebox.showerror("CSV Error", f"Could not save CSV:\n{e}")

        # --- Export PNG ---
        if self.export_png.get():
            filename = f"test_graph_{int(time.time())}.png"
            full_path = os.path.join(self.export_folder, filename)
            try:
                self.fig.savefig(full_path)
            except Exception as e:
                messagebox.showerror("PNG Error", f"Could not save graph image:\n{e}")

    # -----------------------------------
    # Export Results Folder Selection
    # -----------------------------------

    def change_export_folder(self):
        """Open a folder-select dialog and update the export path."""
        folder = filedialog.askdirectory(title="Select Export Folder")

        if folder:
            self.export_folder = folder
            self.export_folder_var.set(folder)
            self.status_var.set("Status: Export folder updated")




    # ============================================
    # CLEANUP
    # ============================================

    def on_close(self):
        self.stop_motor()

        if self.en_task:
            try:
                self.en_task.write([True])
                self.en_task.close()
            except:
                pass

        if self.dir_task:
            try:
                self.dir_task.close()
            except:
                pass

        if self.ai_task:
            try:
                self.ai_task.close()
            except:
                pass

        self.root.destroy()



# ============================================
# MAIN ENTRY POINT
# ============================================

if __name__ == "__main__":
    root = tk.Tk()
    app = TestMachineApp(root)
    root.mainloop()
