import os
import io
import webbrowser
from threading import Timer, Lock
from functools import lru_cache
from flask import Flask, request, jsonify, send_from_directory
import numpy as np
import pandas as pd
import CoolProp.CoolProp as CP

app = Flask(__name__, static_folder='static', static_url_path='/static')

# Serve index.html at root
@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

# Serve static assets
@app.route('/<path:path>')
def static_proxy(path):
    return send_from_directory(app.static_folder, path)

# ==========================================================================
# DWSIM THERMODYNAMIC SOLVER ENGINE (STA THREAD-SAFE)
# ==========================================================================
class DWSIMCalculator:
    def __init__(self):
        self.dwsim_path = r"C:\Users\user\AppData\Local\DWSIM"
        self.enabled = os.path.exists(self.dwsim_path)
        self.flowsheets = {}
        self.initialized = False
        self.lock = Lock()
        
    def initialize(self):
        with self.lock:
            if not self.enabled or self.initialized:
                return
            try:
                import sys
                sys.path.append(self.dwsim_path)
                os.environ["PATH"] = self.dwsim_path + os.pathsep + os.environ["PATH"]
                
                import clr
                clr.AddReference("DWSIM.Automation")
                clr.AddReference("DWSIM.Interfaces")
                clr.AddReference("DWSIM.SharedClasses")
                clr.AddReference("DWSIM.UnitOperations")
                clr.AddReference("DWSIM.Thermodynamics")
                
                from DWSIM.Automation import Automation2
                self.auto = Automation2()
                self.initialized = True
                print("DWSIM Automation Engine initialized successfully!")
            except Exception as e:
                print(f"Error initializing DWSIM: {e}")
                self.enabled = False

    def get_flowsheet(self, fluid_name, package_name="PR"):
        if not self.initialized:
            self.initialize()
            
        fluid_clean = fluid_name.lower().replace(" ", "").replace("-", "")
        
        # Map friendly package name to DWSIM package key
        package_map = {
            "pr": "Peng-Robinson (PR)",
            "pr78": "Peng-Robinson 1978 (PR78)",
            "srk": "Soave-Redlich-Kwong (SRK)",
            "nrtl": "NRTL",
            "uniquac": "UNIQUAC",
            "wilson": "Wilson",
            "steam": "Steam Tables (IAPWS-IF97)"
        }
        dwsim_pkg = package_map.get(package_name.lower().strip(), None)
        
        # Determine fallback if not explicitly matched or if custom
        if not dwsim_pkg:
            if "methanol" in fluid_clean:
                dwsim_pkg = "NRTL"
            elif "water" in fluid_clean:
                dwsim_pkg = "Steam Tables (IAPWS-IF97)"
            else:
                dwsim_pkg = "Peng-Robinson (PR)"
                
        cache_key = (fluid_clean, dwsim_pkg)
        if cache_key in self.flowsheets:
            return self.flowsheets[cache_key]
            
        flowsheet = self.auto.CreateFlowsheet()
        
        compounds = []
        fractions = []
        
        # Dynamic Custom Mixture Parsing: e.g. "N-butane[0.4]&N-pentane[0.6]"
        if "[" in fluid_name and "&" in fluid_name:
            import re
            parts = fluid_name.split("&")
            for p in parts:
                match = re.match(r"([^\[]+)\[([\d\.]+)\]", p.strip())
                if match:
                    comp_name = match.group(1).strip()
                    # Basic mapping to DWSIM standard names if needed, or capitalize
                    compounds.append(comp_name.capitalize())
                    fractions.append(float(match.group(2)))
        elif "mixedrefrigerant" in fluid_clean:
            compounds = ["N-butane", "N-pentane", "N-hexane", "N-heptane"]
            fractions = [0.25, 0.25, 0.25, 0.25]
        elif "air" in fluid_clean:
            compounds = ["Nitrogen", "Oxygen", "Argon"]
            fractions = [0.7812, 0.2096, 0.0092]
        elif "propane" in fluid_clean:
            compounds = ["Propane"]
            fractions = [1.0]
        elif "methanol" in fluid_clean:
            compounds = ["Methanol"]
            fractions = [1.0]
        elif "water" in fluid_clean:
            compounds = ["Water"]
            fractions = [1.0]
        elif "co2" in fluid_clean or "carbondioxide" in fluid_clean:
            compounds = ["Carbon dioxide"]
            fractions = [1.0]
        elif "nitrogen" in fluid_clean:
            compounds = ["Nitrogen"]
            fractions = [1.0]
        elif "oxygen" in fluid_clean:
            compounds = ["Oxygen"]
            fractions = [1.0]
        elif "hydrogen" in fluid_clean:
            compounds = ["Hydrogen"]
            fractions = [1.0]
        elif "helium" in fluid_clean:
            compounds = ["Helium"]
            fractions = [1.0]
        else:
            compounds = ["Water"]
            fractions = [1.0]
            
        for c in compounds:
            flowsheet.AddCompound(c)
            
        flowsheet.CreateAndAddPropertyPackage(dwsim_pkg)
        pp_keys = list(flowsheet.PropertyPackages.Keys)
        pp = flowsheet.PropertyPackages[pp_keys[0]]
        
        from DWSIM.Interfaces.Enums.GraphicObjects import ObjectType
        # Unique name per flowsheet object to avoid duplicate names in identical classes
        safe_pkg_name = dwsim_pkg.replace(" ", "_").replace("(", "").replace(")", "").replace("-", "_")
        stream_raw = flowsheet.AddObject(ObjectType.MaterialStream, 100, 100, f"CalcStream_{fluid_clean}_{safe_pkg_name}")
        stream = stream_raw.GetAsObject()
        stream.PropertyPackage = pp
        
        import System
        stream.SetOverallComposition(System.Array[System.Double](fractions))
        stream.SetMassFlow(1.0)
        
        self.flowsheets[cache_key] = (flowsheet, stream)
        return flowsheet, stream
 
    def get_enthalpy(self, fluid_name, T_celsius, P_bar, package="PR"):
        _, stream = self.get_flowsheet(fluid_name, package)
        T_kelvin = T_celsius + 273.15
        P_pascal = P_bar * 1e5
        stream.SetTemperature(T_kelvin)
        stream.SetPressure(P_pascal)
        stream.Calculate()
        return stream.GetMassEnthalpy()
 
    def get_cp(self, fluid_name, T_celsius, P_bar, package="PR"):
        try:
            h1 = self.get_enthalpy(fluid_name, T_celsius, P_bar, package)
            h2 = self.get_enthalpy(fluid_name, T_celsius + 0.1, P_bar, package)
            cp = (h2 - h1) / 0.1 * 1000.0
            if np.isnan(cp) or np.isinf(cp) or cp <= 0:
                return 1000.0
            return cp
        except Exception as e:
            print(f"DWSIM Cp calculation failed for {fluid_name} with package {package} at {T_celsius} C: {e}")
            return 1000.0

dwsim_calculator = DWSIMCalculator()

def run_on_sta_thread(func, *args, **kwargs):
    import clr
    import System
    from System.Threading import Thread, ThreadStart, ApartmentState
    
    result_holder = {}
    exception_holder = {}
    
    def worker():
        try:
            result_holder["res"] = func(*args, **kwargs)
        except Exception as e:
            exception_holder["ex"] = e
            
    t = Thread(ThreadStart(worker))
    t.SetApartmentState(ApartmentState.STA)
    t.Start()
    t.Join()
    
    if "ex" in exception_holder:
        raise exception_holder["ex"]
    return result_holder.get("res")

# ==========================================================================
# COOLPROP PROPERTY EVALUATION (CACHED & THREAD-SAFE)
# ==========================================================================
coolprop_lock = Lock()

@lru_cache(maxsize=10000)
def get_props_si_cached(output_prop, input_prop1, val1, input_prop2, val2, fluid):
    """Cached wrapper around CoolProp.CoolProp.PropsSI to avoid expensive C++ flash iterations. Thread-safe."""
    with coolprop_lock:
        return CP.PropsSI(output_prop, input_prop1, val1, input_prop2, val2, fluid)

@lru_cache(maxsize=10000)
def get_cp_value(fluid, temp_c, pressure_bar, package="PR", use_dwsim=False):
    """
    Returns specific heat capacity cp in J/kg-K at temp_c (Celsius) and pressure_bar (bar).
    Includes safety fallbacks to handle phase boundaries/supercritical exceptions.
    Cached to make high-resolution discretization solver runs run in milliseconds.
    """
    if use_dwsim and dwsim_calculator.enabled:
        return dwsim_calculator.get_cp(fluid, temp_c, pressure_bar, package)
        
    fluid_clean = fluid.lower().replace(" ", "")
    
    if fluid_clean == 'mixedrefrigerant':
        # Case Study 1 Mixed Refrigerant: equimolar mixture of butane/pentane/hexane/heptane at 7.09 bar.
        # Calculated rigorously using CoolProp and numerical enthalpy derivative.
        fluid = "n-Butane[0.25]&n-Pentane[0.25]&n-Hexane[0.25]&n-Heptane[0.25]"
        T_kelvin = temp_c + 273.15
        P_pascals = pressure_bar * 1e5
        try:
            h1 = get_props_si_cached('Hmass', 'T', T_kelvin, 'P', P_pascals, fluid)
            h2 = get_props_si_cached('Hmass', 'T', T_kelvin + 0.1, 'P', P_pascals, fluid)
            cp = (h2 - h1) / 0.1
            if np.isnan(cp) or np.isinf(cp) or cp <= 0:
                return 2200.0
            return cp
        except Exception:
            return 2200.0

    if fluid.lower() == 'custom' or not fluid:
        return 1000.0 # Default fallback placeholder for custom streams

    # Map friendly names to CoolProp standard names
    fluid_map = {
        'water': 'Water',
        'nitrogen': 'Nitrogen',
        'oxygen': 'Oxygen',
        'propane': 'Propane',
        'methanol': 'Methanol',
        'carbondioxide': 'CarbonDioxide',
        'co2': 'CarbonDioxide',
        'helium': 'Helium',
        'hydrogen': 'Hydrogen',
        'air': 'Air'
    }
    cp_fluid = fluid_map.get(fluid.lower().replace(" ", ""), fluid)

    T_kelvin = temp_c + 273.15
    P_pascals = pressure_bar * 1e5

    # Check bounds or critical states to avoid CoolProp crashes
    try:
        # Get specific heat capacity Cp in J/kg-K
        cp = get_props_si_cached('Cpmass', 'T', T_kelvin, 'P', P_pascals, cp_fluid)
        # Check for NaN or infinite values
        if np.isnan(cp) or np.isinf(cp):
            return 1000.0
        return cp
    except Exception:
        # Fallback extrapolation or default value based on fluid
        try:
            # Try getting Cp at a slightly offset temperature or pressure if at phase boundary
            cp = get_props_si_cached('Cpmass', 'T', T_kelvin + 0.1, 'P', P_pascals, cp_fluid)
            return cp
        except Exception:
            # General generic defaults (approximate average values)
            defaults = {
                'Water': 4184.0,
                'Nitrogen': 1040.0,
                'Oxygen': 918.0,
                'Propane': 2400.0,
                'Methanol': 2530.0,
                'CarbonDioxide': 850.0,
                'Helium': 5193.0,
                'Hydrogen': 14300.0,
                'Air': 1006.0
            }
            return defaults.get(cp_fluid, 1000.0)

# Warm up mixtures on server startup to prevent cloud timeouts
def warmup_engines():
    print("Warming up CoolProp thermodynamic database...")
    try:
        get_cp_value("mixedrefrigerant", 100.0, 7.09, use_dwsim=False)
        get_cp_value("air", 0.0, 60.0, use_dwsim=False)
        print("CoolProp warm-up complete!")
    except Exception as e:
        print(f"CoolProp warm-up warning: {e}")
        
    if dwsim_calculator.enabled:
        print("Warming up DWSIM thermodynamic database...")
        try:
            def worker():
                dwsim_calculator.initialize()
                dwsim_calculator.get_flowsheet("mixedrefrigerant")
                dwsim_calculator.get_flowsheet("air")
                print("DWSIM warm-up complete!")
            run_on_sta_thread(worker)
        except Exception as e:
            print(f"DWSIM warm-up warning: {e}")

warmup_engines()

def evaluate_stream_cp_profile(stream, T_start, T_end, step=1.0, use_dwsim=False):
    """
    Generates a temperature vs. CP (kW/°C) list for a stream over its range.
    CP = mass_flow * cp(T, P) / 1000.0
    """
    flow = stream["flow"] # kg/s
    pressure = stream.get("pressure", 1.0) # bar
    fluid = stream.get("fluid", "water")
    
    temps = np.arange(min(T_start, T_end), max(T_start, T_end) + 0.1, step)
    profile = []
    
    for T in temps:
        cp_j_kg = get_cp_value(fluid, T, pressure, stream.get("package", "PR"), use_dwsim=use_dwsim)
        cp_kw_c = (flow * cp_j_kg) / 1000.0 # convert W/°C to kW/°C
        profile.append((float(T), float(cp_kw_c)))
        
    return profile

# ==========================================================================
# DIFFERENTIAL PROBLEM TABLE ALGORITHM (D-PTA)
# ==========================================================================
def run_differential_pinch(streams, delta_tmin, temp_step=1.0, thermo_engine="dwsim"):
    if dwsim_calculator.enabled and thermo_engine == "dwsim":
        return run_on_sta_thread(run_differential_pinch_core, streams, delta_tmin, temp_step, use_dwsim=True)
    else:
        return run_differential_pinch_core(streams, delta_tmin, temp_step, use_dwsim=False)

def run_differential_pinch_core(streams, delta_tmin, temp_step=1.0, use_dwsim=False):
    """
    Calculates targets and curves using temperature discretization (1°C steps).
    """
    if not streams:
        return {
            "targets": {
                "QHmin": 0, "QCmin": 0, "pinchShifted": 0, "pinchHot": 0, "pinchCold": 0,
                "nMin": 0
            },
            "curves": {},
            "comparison": {}
        }

    shift = delta_tmin / 2.0

    # 1. Determine global shifted temperature range
    global_min_shifted = 9999.0
    global_max_shifted = -9999.0

    for s in streams:
        tin_s = s["Tin"] - shift if s["type"] == "hot" else s["Tin"] + shift
        tout_s = s["Tout"] - shift if s["type"] == "hot" else s["Tout"] + shift
        global_min_shifted = min(global_min_shifted, tin_s, tout_s)
        global_max_shifted = max(global_max_shifted, tin_s, tout_s)

    # Discretize shifted temperature scale
    temp_intervals = np.arange(global_min_shifted, global_max_shifted + 0.1, temp_step)
    num_intervals = len(temp_intervals) - 1

    if num_intervals <= 0:
        return {
            "targets": {
                "QHmin": 0, "QCmin": 0, "pinchShifted": 0, "pinchHot": 0, "pinchCold": 0,
                "nMin": 0
            },
            "curves": {},
            "comparison": {}
        }

    # 2. Calculate interval net heat loads (dH_k)
    dH_k = []
    for k in range(num_intervals):
        T_start = temp_intervals[k+1] # High temp boundary of slice
        T_end = temp_intervals[k]     # Low temp boundary of slice
        T_avg = (T_start + T_end) / 2.0
        
        # Calculate active streams contributions
        sum_cp_hot = 0.0
        sum_cp_cold = 0.0
        
        for s in streams:
            is_hot = s["type"] == "hot"
            # Actual boundaries for this stream
            t_low = min(s["Tin"], s["Tout"])
            t_high = max(s["Tin"], s["Tout"])
            
            # Map interval shifted T_avg to actual stream T
            t_actual = T_avg + shift if is_hot else T_avg - shift
            
            # Check if active in this interval
            if t_low <= t_actual <= t_high:
                # Calculate local CP
                cp_j_kg = get_cp_value(s["fluid"], t_actual, s.get("pressure", 1.0), s.get("package", "PR"), use_dwsim=use_dwsim)
                cp_kw_c = (s["flow"] * cp_j_kg) / 1000.0
                if is_hot:
                    sum_cp_hot += cp_kw_c
                else:
                    sum_cp_cold += cp_kw_c
                    
        # Net heat load in this interval (shifted high down to shifted low)
        dh = (sum_cp_hot - sum_cp_cold) * (T_start - T_end)
        dH_k.append(dh)

    # Reverse interval heat loads list so it runs from highest temp down to lowest
    dH_k.reverse()
    shifted_temps_descending = list(reversed(temp_intervals))

    # 3. Cascade Heat
    R = [0.0]
    for k in range(num_intervals):
        r_next = R[-1] + dH_k[k]
        R.append(r_next)

    # 4. Feasible Cascade
    min_r = min(R)
    qh_min = max(0.0, -min_r)
    R_feasible = [r + qh_min for r in R]
    qc_min = R_feasible[-1]

    # Find Pinch Point (where feasible cascade is closest to zero)
    pinch_idx = min(range(len(R_feasible)), key=lambda i: abs(R_feasible[i]))
    pinch_shifted = float(shifted_temps_descending[pinch_idx])
    pinch_hot = pinch_shifted + shift
    pinch_cold = pinch_shifted - shift

    # 5. Generate Differential GCC Coordinates
    gcc_Q = []
    gcc_T = []
    for idx, T_val in enumerate(shifted_temps_descending):
        gcc_Q.append(float(R_feasible[idx]))
        gcc_T.append(float(T_val))

    # 6. Generate Differential Composite Curves Coordinates
    # Integrate active stream heat loads at each 1°C step
    all_actual_temps = set()
    for s in streams:
        all_actual_temps.add(s["Tin"])
        all_actual_temps.add(s["Tout"])
    
    act_min = min(all_actual_temps)
    act_max = max(all_actual_temps)
    act_intervals = np.arange(act_min, act_max + 0.1, temp_step)
    
    hot_Q_cum = 0.0
    cold_Q_cum = 0.0
    
    hot_H = [0.0]
    hot_T = [float(act_intervals[0])]
    cold_H = [0.0]
    cold_T = [float(act_intervals[0])]

    for k in range(len(act_intervals) - 1):
        T_low = act_intervals[k]
        T_high = act_intervals[k+1]
        T_avg = (T_low + T_high) / 2.0
        
        # Hot Composite integral step
        cp_hot_total = 0.0
        for s in streams:
            if s["type"] == "hot" and min(s["Tin"], s["Tout"]) <= T_avg <= max(s["Tin"], s["Tout"]):
                cp_val = get_cp_value(s["fluid"], T_avg, s.get("pressure", 1.0), s.get("package", "PR"), use_dwsim=use_dwsim)
                cp_hot_total += (s["flow"] * cp_val) / 1000.0
        hot_Q_cum += cp_hot_total * (T_high - T_low)
        hot_H.append(float(hot_Q_cum))
        hot_T.append(float(T_high))

        # Cold Composite integral step
        cp_cold_total = 0.0
        for s in streams:
            if s["type"] == "cold" and min(s["Tin"], s["Tout"]) <= T_avg <= max(s["Tin"], s["Tout"]):
                cp_val = get_cp_value(s["fluid"], T_avg, s.get("pressure", 1.0), s.get("package", "PR"), use_dwsim=use_dwsim)
                cp_cold_total += (s["flow"] * cp_val) / 1000.0
        cold_Q_cum += cp_cold_total * (T_high - T_low)
        cold_H.append(float(cold_Q_cum))
        cold_T.append(float(T_high))

    # Shift Cold Composite by QCmin
    cold_H_shifted = [h + qc_min for h in cold_H]

    # Verify consistency between composite curves and GCC (with numerical tolerance)
    diff = abs((cold_H_shifted[-1] - hot_H[-1]) - qh_min)
    if diff >= 5.0:
        print(f"Warning: Composite curves and GCC inconsistent by {diff:.2f} kW")


    # Calculate actual maximum H scale
    h_max = max(max(hot_H) if hot_H else 0, max(cold_H_shifted) if cold_H_shifted else 0)

    # 7. Generate stream Cp profiles for plotting
    profiles = {}
    for s in streams:
        prof = evaluate_stream_cp_profile(s, s["Tin"], s["Tout"], step=temp_step, use_dwsim=use_dwsim)
        profiles[s["id"]] = prof

    # 8. COMPARE AGAINST CONSTANT CP AVERAGE MODEL
    # First, calculate average CP for each stream: CP_avg = Integral(CP(T) dT) / dT
    constant_streams = []
    for s in streams:
        t_range = abs(s["Tin"] - s["Tout"])
        if t_range < 0.1:
            cp_avg_kw = (s["flow"] * get_cp_value(s["fluid"], s["Tin"], s.get("pressure", 1.0), s.get("package", "PR"), use_dwsim=use_dwsim)) / 1000.0
        else:
            # Numerical integration of Cp
            t_steps = np.linspace(min(s["Tin"], s["Tout"]), max(s["Tin"], s["Tout"]), 50)
            cp_sum = sum(get_cp_value(s["fluid"], t, s.get("pressure", 1.0), s.get("package", "PR"), use_dwsim=use_dwsim) for t in t_steps)
            cp_avg_j_kg = cp_sum / len(t_steps)
            cp_avg_kw = (s["flow"] * cp_avg_j_kg) / 1000.0
            
        constant_streams.append({
            "id": s["id"],
            "type": s["type"],
            "Tin": s["Tin"],
            "Tout": s["Tout"],
            "MCp": cp_avg_kw # Constant CP model uses MCp
        })
        
    const_results = solve_classic_constant_cp(constant_streams, delta_tmin)

    # Calculate errors
    err_qh = float(((qh_min - const_results["QHmin"]) / (qh_min if qh_min > 0 else 1.0)) * 100.0)
    err_qc = float(((qc_min - const_results["QCmin"]) / (qc_min if qc_min > 0 else 1.0)) * 100.0)

    # Number of units target
    # Nmin = N_streams_above + N_streams_below - 2 (approximately)
    hot_above = sum(1 for s in streams if s["type"] == "hot" and max(s["Tin"], s["Tout"]) > pinch_hot)
    cold_above = sum(1 for s in streams if s["type"] == "cold" and max(s["Tin"], s["Tout"]) > pinch_cold)
    hot_below = sum(1 for s in streams if s["type"] == "hot" and min(s["Tin"], s["Tout"]) < pinch_hot)
    cold_below = sum(1 for s in streams if s["type"] == "cold" and min(s["Tin"], s["Tout"]) < pinch_cold)
    n_min = (hot_above + cold_above) + (hot_below + cold_below)

    targets = {
        "QHmin": float(qh_min),
        "QCmin": float(qc_min),
        "pinchShifted": float(pinch_shifted),
        "pinchHot": float(pinch_hot),
        "pinchCold": float(pinch_cold),
        "nMin": int(n_min)
    }

    curves = {
        "hot_H": hot_H,
        "hot_T": hot_T,
        "cold_H_shifted": cold_H_shifted,
        "cold_T": cold_T,
        "gcc_Q": gcc_Q,
        "gcc_T": gcc_T,
        "h_max": float(h_max),
        "t_min": float(act_min),
        "t_max": float(act_max)
    }

    comparison = {
        "constQH": float(const_results["QHmin"]),
        "constQC": float(const_results["QCmin"]),
        "constPinchShifted": float(const_results["pinchShifted"]),
        "errQH": round(err_qh, 1),
        "errQC": round(err_qc, 1)
    }

    return {
        "targets": targets,
        "curves": curves,
        "profiles": profiles,
        "comparison": comparison
    }

# Standard Constant-CP solver for comparison
def solve_classic_constant_cp(streams, delta_tmin):
    shift = delta_tmin / 2.0
    shifted = []
    for s in streams:
        tin_s = s["Tin"] - shift if s["type"] == "hot" else s["Tin"] + shift
        tout_s = s["Tout"] - shift if s["type"] == "hot" else s["Tout"] + shift
        shifted.append({**s, "Tin_s": tin_s, "Tout_s": tout_s})

    all_temps = set()
    for s in shifted:
        all_temps.add(s["Tin_s"])
        all_temps.add(s["Tout_s"])
    temp_list = sorted(list(all_temps), reverse=True)

    Fk = []
    for T in temp_list:
        fk = 0.0
        for s in shifted:
            if abs(T - s["Tin_s"]) < 1e-5: fk += s["MCp"]
            if abs(T - s["Tout_s"]) < 1e-5: fk -= s["MCp"]
        Fk.append(round(fk, 6))

    CumFk = []
    cumulative = 0.0
    for fk in Fk:
        cumulative += fk
        CumFk.append(round(cumulative, 6))

    Qk = [0.0]
    for i in range(1, len(temp_list)):
        q = CumFk[i - 1] * (temp_list[i - 1] - temp_list[i])
        Qk.append(round(q, 6))

    Qcas = []
    cumulative_q = 0.0
    for q in Qk:
        cumulative_q += q
        Qcas.append(round(cumulative_q, 6))

    min_qcas = min(Qcas)
    qh_min = max(0.0, -min_qcas)
    Rcas = [q + qh_min for q in Qcas]
    qc_min = Rcas[-1]
    
    pinch_idx = min(range(len(Rcas)), key=lambda i: abs(Rcas[i]))
    pinch_shifted = temp_list[pinch_idx]

    return {
        "QHmin": qh_min,
        "QCmin": qc_min,
        "pinchShifted": pinch_shifted
    }

# ==========================================================================
# FLASK WEB ENDPOINTS
# ==========================================================================
@app.route('/api/solve', methods=['POST'])
def solve_pinch():
    data = request.get_json() or {}
    streams = data.get("streams", [])
    delta_tmin = float(data.get("deltaTmin", 10.0))
    thermo_engine = data.get("thermoEngine", "dwsim")
    
    results = run_differential_pinch(streams, delta_tmin, thermo_engine=thermo_engine)
    return jsonify(results)

@app.route('/api/upload_excel', methods=['POST'])
def upload_excel():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    try:
        # Read the Excel file into memory
        file_stream = io.BytesIO(file.read())
        
        # Parse Streams Sheet
        df_streams = pd.read_excel(file_stream, sheet_name="Streams")
        streams = []
        for idx, row in df_streams.iterrows():
            if pd.isna(row.get('Stream')):
                continue
            stream = {
                "id": str(row.get("Stream", f"S{idx+1}")),
                "type": str(row.get("Type", "hot")).lower().strip(),
                "fluid": str(row.get("Fluid", "water")).strip(),
                "flow": float(row.get("Flow", 1.0)),
                "pressure": float(row.get("Pressure", 1.0)),
                "Tin": float(row.get("Tin", 0.0)),
                "Tout": float(row.get("Tout", 0.0))
            }
            # Optional Package column
            if "Package" in row and pd.notna(row["Package"]):
                stream["package"] = str(row["Package"]).strip()
                
            # Parse custom properties if present
            if stream["fluid"].lower() == "custom":
                custom_props = {}
                if "Mw" in row and pd.notna(row["Mw"]): custom_props["Mw"] = float(row["Mw"])
                if "Tc" in row and pd.notna(row["Tc"]): custom_props["Tc"] = float(row["Tc"])
                if "Pc" in row and pd.notna(row["Pc"]): custom_props["Pc"] = float(row["Pc"])
                if "omega" in row and pd.notna(row["omega"]): custom_props["omega"] = float(row["omega"])
                if "ConstantCp" in row and pd.notna(row["ConstantCp"]): custom_props["ConstantCp"] = float(row["ConstantCp"])
                stream["custom_props"] = custom_props
                
            streams.append(stream)
            
        # Parse Settings Sheet
        df_settings = pd.read_excel(file_stream, sheet_name="Settings")
        delta_tmin = 20.0
        for idx, row in df_settings.iterrows():
            param = str(row.get("Parameter", "")).lower().strip()
            if param == "tmin" or param == "delta_tmin":
                delta_tmin = float(row.get("Value", 20.0))
                
        return jsonify({"streams": streams, "deltaTmin": delta_tmin})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def open_browser():
    webbrowser.open_new("http://127.0.0.1:5000")

if __name__ == '__main__':
    # Automatically start the web application in a browser
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        Timer(1.5, open_browser).start()
    app.run(host='127.0.0.1', port=5000, debug=True)
