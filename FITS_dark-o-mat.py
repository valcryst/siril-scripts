import os
import re
import json
import shutil
import tkinter as tk
import sirilpy as s
s.ensure_installed("ttkthemes", "astropy.io", "sqlite3")

import sqlite3
from tkinter import ttk, filedialog, messagebox
from ttkthemes import ThemedTk
from astropy.io import fits
from datetime import datetime

LIBRARIES_CONFIG = os.path.expanduser("~/.siril-dark-libraries.json")
DB_DIR = os.path.expanduser("~/.siril-dark-libraries")
os.makedirs(DB_DIR, exist_ok=True)

siril = s.SirilInterface()
print("Loading " + LIBRARIES_CONFIG)

try:
    siril.connect()
    print("Successfully connected to Siril!")
except SirilConnectionError as e:
    print(f"Connection to Siril failed: {e}")

def load_libraries():
    if os.path.exists(LIBRARIES_CONFIG):
        with open(LIBRARIES_CONFIG, 'r') as f:
            return json.load(f)
    return {}

def save_libraries(libraries):
    with open(LIBRARIES_CONFIG, 'w') as f:
        json.dump(libraries, f, indent=2)

def create_db(db_path):
    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS darks (
                id INTEGER PRIMARY KEY,
                path TEXT,
                ccd_temp REAL,
                iso INTEGER,
                gain REAL,
                exptime REAL,
                naxis1 INTEGER,
                naxis2 INTEGER,
                xbinning INTEGER,
                ybinning INTEGER
            )
        """)
        conn.commit()

def scan_directory(dir_path):
    fits_files = []
    for root, _, files in os.walk(dir_path):
        for f in files:
            if f.lower().endswith((".fit", ".fits")):
                fits_files.append(os.path.join(root, f))
    return fits_files

def read_fits_header(file):
    try:
        with fits.open(file) as hdul:
            hdr = hdul[0].header
            temp = hdr.get("CCD-TEMP")
            iso = hdr.get("ISOSPEED")
            gain = hdr.get("GAIN") or hdr.get("EGAIN")
            exptime = hdr.get("EXPTIME")
            naxis1 = hdr.get("NAXIS1")
            naxis2 = hdr.get("NAXIS2")
            xbin = hdr.get("XBINNING")
            ybin = hdr.get("YBINNING")
            if None in (temp, exptime, naxis1, naxis2, xbin, ybin) or (iso is None and gain is None):
                return None
            return {
                "path": file,
                "ccd_temp": temp,
                "iso": iso,
                "gain": gain,
                "exptime": exptime,
                "naxis1": naxis1,
                "naxis2": naxis2,
                "xbinning": xbin,
                "ybinning": ybin
            }
    except Exception as e:
        print(f"Error reading {file}: {e}")
        return None

class dark_o_mat:
    def __init__(self, root):
        self.root = root
        self.root.title("FITS Dark-O-Mat - 2025.08-1")
        self.libraries = load_libraries()
        self.selected_library = tk.StringVar()
        self.create_widgets()
        self.update_library_dropdown()

    def create_widgets(self):
        frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Select existing library:").grid(row=0, column=0, sticky="w")
        self.library_combo = ttk.Combobox(frame, textvariable=self.selected_library, width=40, state="readonly")
        self.library_combo.grid(row=0, column=1, sticky="ew")
        self.library_combo.bind("<<ComboboxSelected>>", lambda e: self.populate_criteria())

        frame.grid_columnconfigure(0, weight=0)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_columnconfigure(2, weight=0)

        ttk.Separator(frame, orient="horizontal").grid(row=1, column=0, columnspan=3, pady=10, sticky="ew")

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=5)
        button_frame.grid_columnconfigure((0, 1, 2), weight=1)
        ttk.Button(button_frame, text="Add new library", command=self.add_new_library_dialog).grid(row=0, column=0, sticky="ew", padx=2)
        ttk.Button(button_frame, text="Delete library",  command=self.delete_library).grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(button_frame, text="Rescan",          command=self.rescan_library).grid(row=0, column=2, sticky="ew", padx=2)

        self.criteria_frame = ttk.LabelFrame(frame, text="Master Dark Settings")
        self.criteria_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=10)
        self.criteria_frame.grid_columnconfigure(0, weight=0)
        self.criteria_frame.grid_columnconfigure(1, weight=1)
        self.criteria_frame.grid_columnconfigure(2, weight=0)

        self.temp_var, self.temp_max_var, self.iso_var, self.exptime_var = (
            tk.StringVar(), tk.StringVar(), tk.StringVar(), tk.StringVar()
        )
        self.res_var, self.bin_var = tk.StringVar(), tk.StringVar()
        self.temp_range_var = tk.BooleanVar(value=False)

        ttk.Label(self.criteria_frame, text="Temperature").grid(row=0, column=0, sticky="w")
        self.temp_cb = ttk.Combobox(
            self.criteria_frame,
            textvariable=self.temp_var,
            state="readonly",
            width=20
        )
        self.temp_cb.grid(row=0, column=1, sticky="ew")
        self.temp_cb.bind("<<ComboboxSelected>>", lambda e: [self.filter_dropdowns_by_temp(), self.update_matching_files(), self.check_all_selected()])
        self.temp_range_cb = ttk.Checkbutton(
            self.criteria_frame,
            text="Min/Max",
            variable=self.temp_range_var,
            command=self.on_temp_range_toggle
        )
        self.temp_range_cb.grid(row=0, column=2, sticky="w")

        ttk.Label(self.criteria_frame, text="Temperature Max").grid(row=1, column=0, sticky="w")
        self.temp_max_cb = ttk.Combobox(
            self.criteria_frame,
            textvariable=self.temp_max_var,
            state="disabled",
            width=20
        )
        self.temp_max_cb.grid(row=1, column=1, sticky="ew")
        self.temp_max_cb.bind("<<ComboboxSelected>>", lambda e: [self.filter_dropdowns_by_temp(), self.update_matching_files(), self.check_all_selected()])

        other_criteria = [
            ("ISO/Gain", self.iso_var, "iso_cb"),
            ("Exposure Time", self.exptime_var, "exptime_cb"),
            ("Resolution", self.res_var, "res_cb"),
            ("Binning", self.bin_var, "bin_cb"),
        ]
        for idx, (label, var, cb_name) in enumerate(other_criteria, start=2):
            ttk.Label(self.criteria_frame, text=label).grid(row=idx, column=0, sticky="w")
            cb = ttk.Combobox(self.criteria_frame, textvariable=var, state="readonly", width=20)
            cb.grid(row=idx, column=1, sticky="ew")
            cb.bind("<<ComboboxSelected>>", lambda e: [self.update_matching_files(), self.check_all_selected()])
            setattr(self, cb_name, cb)

        ttk.Label(self.criteria_frame, text="Matching darks:").grid(row=6, column=0, sticky="w")
        self.matching_count = tk.StringVar(value="0")
        ttk.Label(self.criteria_frame, textvariable=self.matching_count).grid(row=6, column=1, sticky="w")

        ttk.Label(self.criteria_frame, text="Number of darks to use:").grid(row=7, column=0, sticky="w")
        self.slider_value = tk.StringVar(value="2")
        ttk.Label(self.criteria_frame, textvariable=self.slider_value).grid(row=7, column=1, sticky="w")

        self.slider = ttk.Scale(
            self.criteria_frame, from_=2, to=2, orient="horizontal",
            command=self.update_slider_label
        )
        self.slider.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(2, 10))

        ttk.Button(
            self.criteria_frame,
            text="Select target directory",
            command=self.select_target_dir
        ).grid(row=9, column=0, pady=(10, 0), sticky="w")
        self.target_dir = tk.StringVar()
        ttk.Label(self.criteria_frame, textvariable=self.target_dir).grid(row=9, column=1, padx=10, sticky="ew")

        self.create_btn = ttk.Button(
            self.criteria_frame,
            text="Create Master Dark",
            command=self.create_master_dark,
            state="disabled"
        )
        self.create_btn.grid(row=10, column=0, columnspan=2, pady=10)

    def on_temp_range_toggle(self):
        if self.temp_range_var.get():
            self.temp_cb.config(state="readonly")
            self.temp_max_cb.config(state="readonly")
        else:
            self.temp_max_var.set("")
            self.temp_max_cb.config(state="disabled")
        self.filter_dropdowns_by_temp()
        self.update_matching_files()
        self.check_all_selected()

    def update_slider_label(self, _):
        self.slider_value.set(str(int(float(self.slider.get()))))

    def update_library_dropdown(self):
        self.library_combo["values"] = list(self.libraries.keys())

    def add_new_library_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Add new library")
        ttk.Label(dialog, text="Library name:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        name_var, path_var = tk.StringVar(), tk.StringVar()
        ttk.Entry(dialog, textvariable=name_var, width=30).grid(row=0, column=1, padx=5, pady=5)
        ttk.Label(dialog, text="Source directory:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(dialog, textvariable=path_var, width=30, state="readonly").grid(row=1, column=1, padx=5, pady=5)

        def browse():
            dirpath = filedialog.askdirectory(title="Select dark source directory")
            if dirpath:
                path_var.set(dirpath)

        ttk.Button(dialog, text="Browse", command=browse).grid(row=1, column=2, padx=5, pady=5)

        def confirm():
            name, path = name_var.get().strip(), path_var.get().strip()
            if not name or not path:
                messagebox.showerror("Error", "Name and directory must be provided.")
                return
            db_path = os.path.join(DB_DIR, f"{name}.sqlite")
            self.libraries[name] = {"path": path, "db": db_path}
            save_libraries(self.libraries)
            create_db(db_path)
            fits_files = scan_directory(path)
            resp = messagebox.askyesno("Scan results", f"Found {len(fits_files)} FITS files. Proceed with inventarisation?")
            if not resp:
                dialog.destroy()
                return
            with sqlite3.connect(db_path) as conn:
                c = conn.cursor()
                for f in fits_files:
                    entry = read_fits_header(f)
                    if entry:
                        c.execute("""
                            INSERT INTO darks (path, ccd_temp, iso, gain, exptime,
                                               naxis1, naxis2, xbinning, ybinning)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                  (entry["path"], entry["ccd_temp"], entry["iso"],
                                   entry["gain"], entry["exptime"], entry["naxis1"],
                                   entry["naxis2"], entry["xbinning"], entry["ybinning"]))
                conn.commit()
            self.update_library_dropdown()
            self.selected_library.set(name)
            dialog.destroy()
            messagebox.showinfo("Scan complete", f"Library '{name}' created and scanned successfully.")
            self.populate_criteria()

        ttk.Button(dialog, text="Create library", command=confirm).grid(row=2, column=0, columnspan=3, pady=10)

    def delete_library(self):
        name = self.selected_library.get()
        if not name:
            messagebox.showerror("Error", "Please select a library to delete.")
            return
        confirm = messagebox.askyesno("Confirm delete", f"Really delete library '{name}'?\nThis cannot be undone! (fits remain untouched)")
        if not confirm:
            return
        db_info = self.libraries.pop(name, None)
        if db_info:
            db_path = db_info["db"]
            try:
                if os.path.exists(db_path):
                    os.remove(db_path)
            except Exception as e:
                messagebox.showerror("Error", f"Could not delete database file:\n{e}")
                return
            save_libraries(self.libraries)
            self.update_library_dropdown()
            self.selected_library.set("")
            messagebox.showinfo("Deleted", f"Library '{name}' has been deleted.")

    def rescan_library(self):
        name = self.selected_library.get()
        if not name:
            messagebox.showerror("Error", "Please select a library to rescan.")
            return
        db_info = self.libraries[name]
        db_path = db_info["db"]
        dir_path = db_info["path"]
        fits_files = scan_directory(dir_path)
        resp = messagebox.askyesno("Rescan results", f"Found {len(fits_files)} FITS files. Proceed with inventarisation?")
        if not resp:
            return
        with sqlite3.connect(db_path) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM darks")
            for f in fits_files:
                entry = read_fits_header(f)
                if entry:
                    c.execute("""
                        INSERT INTO darks (path, ccd_temp, iso, gain, exptime,
                                           naxis1, naxis2, xbinning, ybinning)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                              (entry["path"], entry["ccd_temp"], entry["iso"],
                               entry["gain"], entry["exptime"], entry["naxis1"],
                               entry["naxis2"], entry["xbinning"], entry["ybinning"]))
            conn.commit()
        messagebox.showinfo("Rescan complete", "Library database updated.")
        self.populate_criteria()

    def populate_criteria(self):
        name = self.selected_library.get()
        if not name:
            return
        db = self.libraries[name]["db"]
        with sqlite3.connect(db) as conn:
            c = conn.cursor()
            c.execute("SELECT DISTINCT ccd_temp FROM darks")
            raw_temps = [str(r[0]) for r in c.fetchall()]
            try:
                temp_vals = sorted(raw_temps, key=lambda v: float(v))
            except ValueError:
                temp_vals = sorted(raw_temps)
            self.temp_cb.config(values=temp_vals)
            self.temp_max_cb.config(values=temp_vals)
            self.temp_var.set("")
            self.temp_max_var.set("")

            for col, var, cb_name in [
                ("iso", self.iso_var, "iso_cb"),
                ("exptime", self.exptime_var, "exptime_cb"),
                ("naxis1 || 'x' || naxis2", self.res_var, "res_cb"),
                ("xbinning || 'x' || ybinning", self.bin_var, "bin_cb"),
            ]:
                c.execute(f"SELECT DISTINCT {col} FROM darks")
                raw_values = [str(row[0]) for row in c.fetchall()]
                try:
                    cb_values = sorted(raw_values, key=lambda v: float(v.split("x")[0]) if "x" in v else float(v))
                except ValueError:
                    cb_values = sorted(raw_values)
                getattr(self, cb_name).config(values=cb_values)
                var.set("")

        self.update_matching_files()
        self.check_all_selected()

    def check_all_selected(self):
        needed = []
        if self.temp_range_var.get():
            needed += [self.temp_var.get(), self.temp_max_var.get()]
        else:
            needed += [self.temp_var.get()]
        needed += [
            self.iso_var.get(),
            self.exptime_var.get(),
            self.res_var.get(),
            self.bin_var.get()
        ]
        if all(needed):
            self.create_btn.config(state="normal")
        else:
            self.create_btn.config(state="disabled")

    def filter_dropdowns_by_temp(self):
        name = self.selected_library.get()
        if not name:
            return
        db = self.libraries[name]["db"]
        where = ""
        params = []
        tmin = self.temp_var.get().strip()
        if self.temp_range_var.get():
            tmax = self.temp_max_var.get().strip()
            if tmin and tmax:
                where = " WHERE ccd_temp BETWEEN ? AND ?"
                params = [float(tmin), float(tmax)]
        else:
            if tmin:
                where = " WHERE ccd_temp = ?"
                params = [float(tmin)]
        with sqlite3.connect(db) as conn:
            c = conn.cursor()
            c.execute("SELECT DISTINCT iso FROM darks" + where, params)
            raw_iso = [str(r[0]) for r in c.fetchall()]
            try:
                iso_vals = sorted(raw_iso, key=lambda v: float(v))
            except:
                iso_vals = sorted(raw_iso)
            self.iso_cb.config(values=iso_vals)
            if self.iso_var.get() not in iso_vals:
                self.iso_var.set("")

            c.execute("SELECT DISTINCT exptime FROM darks" + where, params)
            raw_exp = [str(r[0]) for r in c.fetchall()]
            try:
                exp_vals = sorted(raw_exp, key=lambda v: float(v))
            except:
                exp_vals = sorted(raw_exp)
            self.exptime_cb.config(values=exp_vals)
            if self.exptime_var.get() not in exp_vals:
                self.exptime_var.set("")

            c.execute("SELECT DISTINCT naxis1 || 'x' || naxis2 FROM darks" + where, params)
            raw_res = [str(r[0]) for r in c.fetchall()]
            try:
                res_vals = sorted(raw_res, key=lambda v: float(v.split("x")[0]) if "x" in v else float(v))
            except:
                res_vals = sorted(raw_res)
            self.res_cb.config(values=res_vals)
            if self.res_var.get() not in res_vals:
                self.res_var.set("")

            c.execute("SELECT DISTINCT xbinning || 'x' || ybinning FROM darks" + where, params)
            raw_bin = [str(r[0]) for r in c.fetchall()]
            try:
                bin_vals = sorted(raw_bin, key=lambda v: float(v.split("x")[0]) if "x" in v else float(v))
            except:
                bin_vals = sorted(raw_bin)
            self.bin_cb.config(values=bin_vals)
            if self.bin_var.get() not in bin_vals:
                self.bin_var.set("")

    def update_matching_files(self):
        name = self.selected_library.get()
        if not name:
            return
        db = self.libraries[name]["db"]
        where, params = [], []
        tmin = self.temp_var.get().strip()
        if self.temp_range_var.get():
            tmax = self.temp_max_var.get().strip()
            if tmin and tmax:
                where.append("ccd_temp BETWEEN ? AND ?")
                params.extend([float(tmin), float(tmax)])
        else:
            if tmin:
                where.append("ccd_temp = ?")
                params.append(float(tmin))
        for col, var in [("iso", self.iso_var),
                         ("exptime", self.exptime_var),
                         ("naxis1 || 'x' || naxis2", self.res_var),
                         ("xbinning || 'x' || ybinning", self.bin_var)]:
            val = var.get().strip()
            if val:
                where.append(f"{col} = ?")
                params.append(val)
        query = "SELECT COUNT(*) FROM darks"
        if where:
            query += " WHERE " + " AND ".join(where)
        with sqlite3.connect(db) as conn:
            c = conn.cursor()
            c.execute(query, params)
            count = c.fetchone()[0]
        self.matching_count.set(str(count))

        if count >= 2:
            self.slider.config(from_=2, to=count)
            self.slider.set(min(2, count))
        else:
            self.slider.config(from_=2, to=2)
            self.slider.set(2)
        self.update_slider_label(None)

    def select_target_dir(self):
        dirpath = filedialog.askdirectory(title="Select target directory")
        if dirpath:
            self.target_dir.set(dirpath)

    def create_master_dark(self):
        if not self.target_dir.get():
            messagebox.showerror("Error", "Please select a target directory first.")
            return
        num_to_stack = int(float(self.slider.get()))
        name = self.selected_library.get()
        if not name:
            return
        db = self.libraries[name]["db"]
        where, params = [], []
        tmin = self.temp_var.get().strip()
        if self.temp_range_var.get():
            tmax = self.temp_max_var.get().strip()
            if tmin and tmax:
                where.append("ccd_temp BETWEEN ? AND ?")
                params.extend([float(tmin), float(tmax)])
        else:
            if tmin:
                where.append("ccd_temp = ?")
                params.append(float(tmin))
        for col, var in [("iso", self.iso_var),
                         ("exptime", self.exptime_var),
                         ("naxis1 || 'x' || naxis2", self.res_var),
                         ("xbinning || 'x' || ybinning", self.bin_var)]:
            val = var.get().strip()
            if val:
                where.append(f"{col} = ?")
                params.append(val)
        query = "SELECT path FROM darks"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " LIMIT ?"
        params.append(num_to_stack)
        with sqlite3.connect(db) as conn:
            c = conn.cursor()
            c.execute(query, params)
            files = [row[0] for row in c.fetchall()]
        if len(files) < 2:
            messagebox.showerror("Error", "At least 2 darks required for stacking.")
            return

        tmpdir = self.target_dir.get() + "/master_dark_tmp"
        if os.path.exists(tmpdir):
            shutil.rmtree(tmpdir)
        os.makedirs(tmpdir)

        lib_name = re.sub(r'[^A-Za-z0-9_-]', '_', name)
        exptime_dir = f"{self.exptime_var.get()}s"
        outdir = os.path.join(self.target_dir.get(), lib_name, exptime_dir)
        os.makedirs(outdir, exist_ok=True)

        for f in files:
            shutil.copy(f, tmpdir)

        master_name = self.generate_master_name()
        master_path = os.path.join(outdir, master_name)
        siril.cmd("cd", tmpdir)
        siril.cmd("convert", "seq_dark")
        siril.cmd("stack", "seq_dark", "rej", "3", "3", f"-out={master_path}")
        siril.cmd("cd", "..")
        shutil.rmtree(tmpdir)
        messagebox.showinfo("Done", f"Master dark created: {master_path}")

    def generate_master_name(self):
        iso = self.iso_var.get()
        exptime = self.exptime_var.get()
        if self.temp_range_var.get():
            temp = f"{self.temp_var.get()}-{self.temp_max_var.get()}"
        else:
            temp = self.temp_var.get()
        binning = self.bin_var.get()
        resolution = self.res_var.get()
        stack_cnt = int(float(self.slider.get()))
        current_date = datetime.today().strftime('%Y-%m-%d')
        return f"master-dark_iso{iso}_{exptime}s_{temp}c_{resolution}_bin{binning}_{stack_cnt}x_{current_date}.fit"

if __name__ == "__main__":
    root = ThemedTk(theme="equilux")
    root.geometry("412x516")
    root.resizable(False, False)
    app = dark_o_mat(root)
    root.mainloop()
