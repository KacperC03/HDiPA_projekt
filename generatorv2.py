import csv
import math
import os
import random
import psycopg2
from psycopg2.extras import execute_values
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import config
import db_config
# ==============================================================================
# 1. KONFIGURACJA GŁÓWNA
# ==============================================================================
TRYB_ZAPISU = config.write_mode 

DB_CONFIG = {
    "host": db_config.host,
    "database": db_config.database,
    "user": db_config.user,
    "password": db_config.password,
    "port": db_config.port
}

NUM_ROBOTS = config.robot_num         
AVG_MOVING = config.avg_move           
DURATION_MINUTES = config.duration    
START_TIMESTAMP = config.start_timestamp 

SAMPLING_RATE = config.sampling_rate     
DT = 1.0 / SAMPLING_RATE
TOTAL_STEPS = int(DURATION_MINUTES * 60 * SAMPLING_RATE)
CHUNK_SIZE = db_config.chunk_size      

BASE_LAT = config.lat
BASE_LON = config.lon



# ==============================================================================
# 2. MODEL ŚWIATA: JEDNA STREFA GEOFENCE + SZERSZA GRANICA SYMULACJI
# ==============================================================================
# Dozwolona strefa (Geofence)
GEO_MIN_LON, GEO_MAX_LON = BASE_LON - config.poly_size, BASE_LON + config.poly_size
GEO_MIN_LAT, GEO_MAX_LAT = BASE_LAT - config.poly_size, BASE_LAT + config.poly_size

geofence_coords = [
    (GEO_MIN_LON, GEO_MIN_LAT), (GEO_MAX_LON, GEO_MIN_LAT),
    (GEO_MAX_LON, GEO_MAX_LAT), (GEO_MIN_LON, GEO_MAX_LAT)
]

geofences_raw = [{"id": 1, "name": "Glowna_Strefa_Operacyjna", "coords": geofence_coords}]

# Granica fizyczna symulacji
SIM_MIN_LON, SIM_MAX_LON = BASE_LON - (config.poly_size+config.margin), BASE_LON + (config.poly_size+config.margin)
SIM_MIN_LAT, SIM_MAX_LAT = BASE_LAT - (config.poly_size+config.margin), BASE_LAT + (config.poly_size+config.margin)

# ==============================================================================
# CONFIG CZUJNIKÓW: Pełna kontrola nad funkcją aktywacji
# ==============================================================================
sensors_raw = [
    {"id": 101, "lon": BASE_LON + 0.002, "lat": BASE_LAT + 0.002, "period": 200, "duty_cycle": 0.5, "random_anomaly": 0.01},
    {"id": 102, "lon": BASE_LON - 0.002, "lat": BASE_LAT + 0.003, "period": 400, "duty_cycle": 0.2, "random_anomaly": 0.00},
    {"id": 103, "lon": BASE_LON + 0.001, "lat": BASE_LAT - 0.002, "period": 150, "duty_cycle": 0.7, "random_anomaly": 0.05},
    {"id": 104, "lon": BASE_LON - 0.003, "lat": BASE_LAT - 0.003, "period": 300, "duty_cycle": 0.5, "random_anomaly": 0.02},
    {"id": 105, "lon": BASE_LON,         "lat": BASE_LAT,         "period": 600, "duty_cycle": 0.1, "random_anomaly": 0.01}
]

def to_wkt_polygon(coords):
    wkt_pts = ", ".join([f"{lon} {lat}" for lon, lat in coords])
    wkt_pts += f", {coords[0][0]} {coords[0][1]}" 
    return f"POLYGON(({wkt_pts}))"

# ==============================================================================
# 3. INICJALIZACJA REPOZYTORIUM
# ==============================================================================
print(f"[URUCHOMIENIE] Tryb: {TRYB_ZAPISU}")
conn, cursor = None, None
files_handles = []

if TRYB_ZAPISU == "DB":
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("TRUNCATE TABLE robots_telemetry, sensors_data, geofences, sensors_static CASCADE;")
        for gf in geofences_raw:
            cursor.execute("INSERT INTO geofences (id, name, geom_poly) VALUES (%s, %s, ST_GeomFromText(%s, 4326));", (gf["id"], gf["name"], to_wkt_polygon(gf["coords"])))
        for s in sensors_raw:
            cursor.execute("INSERT INTO sensors_static (sensor_id, latitude, longitude, geom_pt) VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326));", (s["id"], s["lat"], s["lon"], s["lon"], s["lat"]))
        conn.commit()
    except Exception as e:
        print(f"[BŁĄD DB] {e}"); exit()
else:
    os.makedirs(config.datagen_dir, exist_ok=True)
    with open(os.path.join(config.datagen_dir,"geofences.csv"), "w", newline="") as f:
        csv.writer(f).writerow(["id", "name", "wkt_geometry"])
        csv.writer(f).writerow([1, "Glowna_Strefa_Operacyjna", to_wkt_polygon(geofence_coords)])
    with open(os.path.join(config.datagen_dir,"sensors_static.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["sensor_id", "latitude", "longitude"])
        for s in sensors_raw: w.writerow([s["id"], s["lat"], s["lon"]])
    f_telemetry = open(os.path.join(config.datagen_dir,"robots_telemetry.csv"), "w", newline="")
    f_sensors = open(os.path.join(config.datagen_dir,"sensors_data.csv"), "w", newline="")
    files_handles.extend([f_telemetry, f_sensors])
    writer_telemetry, writer_sensors = csv.writer(f_telemetry), csv.writer(f_sensors)
    writer_telemetry.writerow(["Id", "timestamp", "latitude", "longitude"])
    writer_sensors.writerow(["sensor_id", "timestamp", "is_threshold_exceeded"])

# ==============================================================================
# 4. INICJALIZACJA NOWEGO KINEMATYCZNEGO MODELU RUCHU (CRW)
# ==============================================================================
robots = {}
for r_id in range(1, NUM_ROBOTS + 1):
    robots[r_id] = {
        "lon": random.uniform(GEO_MIN_LON, GEO_MAX_LON),
        "lat": random.uniform(GEO_MIN_LAT, GEO_MAX_LAT),
        "heading": random.uniform(0, 2 * math.pi), # Kąt poruszania się w radianach
        "state": "MOVING" if r_id <= AVG_MOVING else "IDLE",
        "speed": random.uniform(0.00003, 0.00006), # Prędkość kątowa/krokowa
        "history_lon": [], "history_lat": []
    }

telemetry_buffer, sensor_data_buffer = [], []
current_time = START_TIMESTAMP

# ==============================================================================
# 5. SYSTALACJA I PĘTLA GŁÓWNA
# ==============================================================================
print("[SILNIK] Symulacja ciągłego pokrywania przestrzeni...")

for step in range(TOTAL_STEPS):
    for s in sensors_raw:
        # Wyliczenie fazy cyklu (wartość od 0.0 do 1.0)
        phase = (current_time % s["period"]) / s["period"]
        is_exceeded = 1 if phase < s["duty_cycle"] else 0
        
        # Wstrzyknięcie losowej anomalii (szumu)
        if s["random_anomaly"] > 0 and random.random() < s["random_anomaly"]:
            is_exceeded = 1 - is_exceeded # Odwrócenie stanu
            
        if TRYB_ZAPISU == "DB":
            sensor_data_buffer.append((s["id"], current_time, is_exceeded))
        else:
            sensor_data_buffer.append([s["id"], f"{current_time:.3f}", is_exceeded])
        
    moving_count = sum(1 for r in robots.values() if r["state"] == "MOVING")
    
    for r_id, r in robots.items():
        if r["state"] == "MOVING":
            r["heading"] += random.uniform(-0.25, 0.25)
            next_lon = r["lon"] + math.cos(r["heading"]) * r["speed"]
            next_lat = r["lat"] + math.sin(r["heading"]) * r["speed"]
            if not (SIM_MIN_LON <= next_lon <= SIM_MAX_LON) or not (SIM_MIN_LAT <= next_lat <= SIM_MAX_LAT):
                r["heading"] = math.atan2(BASE_LAT - r["lat"], BASE_LON - r["lon"]) + random.uniform(-0.5, 0.5)
                r["lon"] += math.cos(r["heading"]) * r["speed"]
                r["lat"] += math.sin(r["heading"]) * r["speed"]
            else:
                r["lon"], r["lat"] = next_lon, next_lat
                
            # Szansa na zatrzymanie robota
            if moving_count > AVG_MOVING and random.random() < 0.002:
                r["state"] = "IDLE"
                moving_count -= 1
        else:
            # Szansa na ruszenie z miejsca
            if moving_count < AVG_MOVING and random.random() < 0.01:
                r["state"] = "MOVING"
                r["heading"] = random.uniform(0, 2 * math.pi)
                moving_count += 1
                
        if TRYB_ZAPISU == "DB":
            telemetry_buffer.append((r_id, current_time, r["lat"], r["lon"], r["lon"], r["lat"]))
        else:
            telemetry_buffer.append([r_id, f"{current_time:.3f}", f"{r['lat']:.7f}", f"{r['lon']:.7f}"])
        
        if step % 5 == 0:
            r["history_lon"].append(r["lon"])
            r["history_lat"].append(r["lat"])
            
    # Masowy zrzut danych
    if len(telemetry_buffer) >= CHUNK_SIZE:
        if TRYB_ZAPISU == "DB":
            execute_values(cursor, "INSERT INTO robots_telemetry (robot_id, timestamp, latitude, longitude, geom_pt) VALUES %s", telemetry_buffer, template="(%s, to_timestamp(%s), %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))")
            execute_values(cursor, "INSERT INTO sensors_data (sensor_id, timestamp, is_threshold_exceeded) VALUES %s", sensor_data_buffer, template="(%s, to_timestamp(%s), %s)")
            conn.commit()
        else:
            writer_telemetry.writerows(telemetry_buffer)
            writer_sensors.writerows(sensor_data_buffer)
        telemetry_buffer.clear(); sensor_data_buffer.clear()

    current_time += DT

# Czyszczenie buforów końcowych
if telemetry_buffer:
    if TRYB_ZAPISU == "DB":
        execute_values(cursor, "INSERT INTO robots_telemetry (robot_id, timestamp, latitude, longitude, geom_pt) VALUES %s", telemetry_buffer, template="(%s, to_timestamp(%s), %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))")
        execute_values(cursor, "INSERT INTO sensors_data (sensor_id, timestamp, is_threshold_exceeded) VALUES %s", sensor_data_buffer, template="(%s, to_timestamp(%s), %s)")
        conn.commit()
    else:
        writer_telemetry.writerows(telemetry_buffer)
        writer_sensors.writerows(sensor_data_buffer)

if TRYB_ZAPISU == "DB":
    cursor.close(); conn.close()
else:
    for h in files_handles: h.close()

# ==============================================================================
# 6. GENEROWANIE MAPY OPERACYJNEJ
# ==============================================================================
print("[WIZUALIZACJA] Rysowanie mapy pokrycia terenu...")
fig, ax = plt.subplots(figsize=(11, 9))

gf = geofences_raw[0]
poly_patch = patches.Polygon(gf["coords"], closed=True, linewidth=3, edgecolor='darkred', linestyle='--', facecolor='red', alpha=0.08, label="Granica Geofence")
ax.add_patch(poly_patch)
ax.text(BASE_LON, GEO_MAX_LAT - 0.0004, "STREFA DOZWOLONA (GEOFENCE)", color='darkred', fontsize=12, weight='bold', ha='center')

sim_patch = patches.Rectangle((SIM_MIN_LON, SIM_MIN_LAT), SIM_MAX_LON-SIM_MIN_LON, SIM_MAX_LAT-SIM_MIN_LAT, linewidth=1, edgecolor='gray', linestyle=':', facecolor='none', label="Fizyczny Limit Świata")
ax.add_patch(sim_patch)

for s in sensors_raw:
    ax.scatter(s["lon"], s["lat"], color='blue', marker='^', s=120, zorder=4, label="Czujnik" if s["id"]==101 else "")
    ax.text(s["lon"]+0.00015, s["lat"]+0.00015, f"ID:{s['id']}\nDC:{int(s['duty_cycle']*100)}%", color='blue', fontsize=8, weight='bold')

for r_id, r in robots.items():
    if r["history_lon"]:
        ax.plot(r["history_lon"], r["history_lat"], linewidth=0.8, alpha=0.5)
        ax.scatter(r["history_lon"][-1], r["history_lat"][-1], s=30, edgecolor='black', zorder=5)

ax.set_title(f"Zrównoważone pokrycie przestrzeni metodą CRW (Tryb: {TRYB_ZAPISU})\nWidoczne wyjazdy robotów poza czerwoną linię Geofence", fontsize=12, weight='bold')
ax.set_xlabel("Longitude")
ax.set_ylabel("Latitude")
ax.grid(True, linestyle='--', alpha=0.3)
ax.legend(loc="upper right")
ax.set_aspect('equal', 'datalim')

output_image = os.path.join(config.datagen_dir,"syntetyczna_mapa_pokrycia.png")
plt.tight_layout()
plt.savefig(output_image, dpi=200)
plt.close()
print(f"[SUKCES] Nowa mapa została wygenerowana i zapisana jako: '{output_image}'")