import os
import io
import webbrowser
from threading import Timer
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
# THERMODYNAMIC PROPERTY EVALUATION
# ==========================================================================
def get_cp_value(fluid, temp_c, pressure_bar):
    """
    Returns specific heat capacity cp in J/kg-K at temp_c (Celsius) and pressure_bar (bar).
    Includes safety fallbacks to handle phase boundaries/supercritical exceptions.
    """
    fluid_clean = fluid.lower().replace(" ", "")
    
    if fluid_clean == 'mixedrefrigerant':
        # Case Study 1 Mixed Refrigerant: equimolar mixture of butane/pentane/hexane/heptane at 7.09 bar.
        # Calculated rigorously using CoolProp and numerical enthalpy derivative.
        fluid = "n-Butane[0.25]&n-Pentane[0.25]&n-Hexane[0.25]&n-Heptane[0.25]"
        T_kelvin = temp_c + 273.15
        P_pascals = pressure_bar * 1e5
        try:
            h1 = CP.PropsSI('Hmass', 'T', T_kelvin, 'P', P_pascals, fluid)
            h2 = CP.PropsSI('Hmass', 'T', T_kelvin + 0.1, 'P', P_pascals, fluid)
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
        cp = CP.PropsSI('Cpmass', 'T', T_kelvin, 'P', P_pascals, cp_fluid)
        # Check for NaN or infinite values
        if np.isnan(cp) or np.isinf(cp):
            return 1000.0
        return cp
    except Exception:
        # Fallback extrapolation or default value based on fluid
        try:
            # Try getting Cp at a slightly offset temperature or pressure if at phase boundary
            cp = CP.PropsSI('Cpmass', 'T', T_kelvin + 0.1, 'P', P_pascals, cp_fluid)
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

def evaluate_stream_cp_profile(stream, T_start, T_end, step=1.0):
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
        cp_j_kg = get_cp_value(fluid, T, pressure)
        cp_kw_c = (flow * cp_j_kg) / 1000.0 # convert W/°C to kW/°C
        profile.append((float(T), float(cp_kw_c)))
        
    return profile

# ==========================================================================
# DIFFERENTIAL PROBLEM TABLE ALGORITHM (D-PTA)
# ==========================================================================
def run_differential_pinch(streams, delta_tmin, temp_step=1.0):
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
                cp_j_kg = get_cp_value(s["fluid"], t_actual, s.get("pressure", 1.0))
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
    # We run temperature intervals from lowest to highest actual temperatures
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
                cp_val = get_cp_value(s["fluid"], T_avg, s.get("pressure", 1.0))
                cp_hot_total += (s["flow"] * cp_val) / 1000.0
        hot_Q_cum += cp_hot_total * (T_high - T_low)
        hot_H.append(float(hot_Q_cum))
        hot_T.append(float(T_high))

        # Cold Composite integral step
        cp_cold_total = 0.0
        for s in streams:
            if s["type"] == "cold" and min(s["Tin"], s["Tout"]) <= T_avg <= max(s["Tin"], s["Tout"]):
                cp_val = get_cp_value(s["fluid"], T_avg, s.get("pressure", 1.0))
                cp_cold_total += (s["flow"] * cp_val) / 1000.0
        cold_Q_cum += cp_cold_total * (T_high - T_low)
        cold_H.append(float(cold_Q_cum))
        cold_T.append(float(T_high))

    # Shift Cold Composite by QCmin
    cold_H_shifted = [h + qc_min for h in cold_H]

    # Verify consistency between composite curves and GCC (with numerical tolerance)
    assert abs((cold_H_shifted[-1] - hot_H[-1]) - qh_min) < 2.0, "Composite curves and GCC inconsistent"

    # Calculate actual maximum H scale
    h_max = max(max(hot_H) if hot_H else 0, max(cold_H_shifted) if cold_H_shifted else 0)

    # 7. Generate stream Cp profiles for plotting
    profiles = {}
    for s in streams:
        prof = evaluate_stream_cp_profile(s, s["Tin"], s["Tout"], step=temp_step)
        profiles[s["id"]] = prof

    # 8. COMPARE AGAINST CONSTANT CP AVERAGE MODEL
    # First, calculate average CP for each stream: CP_avg = Integral(CP(T) dT) / dT
    constant_streams = []
    for s in streams:
        t_range = abs(s["Tin"] - s["Tout"])
        if t_range < 0.1:
            cp_avg_kw = (s["flow"] * get_cp_value(s["fluid"], s["Tin"], s.get("pressure", 1.0))) / 1000.0
        else:
            # Numerical integration of Cp
            t_steps = np.linspace(min(s["Tin"], s["Tout"]), max(s["Tin"], s["Tout"]), 50)
            cp_sum = sum(get_cp_value(s["fluid"], t, s.get("pressure", 1.0)) for t in t_steps)
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
    
    results = run_differential_pinch(streams, delta_tmin)
    return jsonify(results)

def open_browser():
    webbrowser.open_new("http://127.0.0.1:5000")

if __name__ == '__main__':
    # Automatically start the web application in a browser
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        Timer(1.5, open_browser).start()
    app.run(host='127.0.0.1', port=5000, debug=True)
