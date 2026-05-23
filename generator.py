import csv
import math
import os
import random
import psycopg2
from psycopg2.extras import execute_values
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import config

# ==============================================================================
# 1. PARAMETR STERUJĄCY (KLUCZOWA ZMIANA)
# ==============================================================================
# Wybierz tryb działania generatora:
# "DB"  - Strumieniowanie w locie bezpośrednio do PostgreSQL + PostGIS
# "CSV" - Klasyczny zapis do 4 plików CSV w folderze projektu
TRYB_ZAPISU = "CSV" 

# ==============================================================================
# CONFIG BAZY DANYCH (Używany tylko gdy TRYB_ZAPISU = "DB")
# ==============================================================================
DB_CONFIG = {
    "host": "localhost",
    "database": "twoja_baza",
    "user": "postgres",
    "password": "twoje_haslo",
    "port": 5232
}

# ==============================================================================
# PARAMETR SYMULACJI
# ==============================================================================
NUM_ROBOTS = config.robot_num       
AVG_MOVING = config.avg_move         
DURATION_MINUTES = config.duration   
START_TIMESTAMP = config.start_timestamp

SAMPLING_RATE = config.sampling_rate     
DT = 1.0 / SAMPLING_RATE
TOTAL_STEPS = int(DURATION_MINUTES * 60 * SAMPLING_RATE)
CHUNK_SIZE = 50000       

BASE_LAT = config.lat
BASE_LON = config.lon

# ==============================================================================
# 2. DEFINICJA GEOMETRII ŚWIATA
# ==============================================================================
geofences_raw = [
    {
        "id": 1, "name": "Pole_Polnoc",
        "coords": [(BASE_LON-0.005, BASE_LAT+0.002), (BASE_LON+0.005, BASE_LAT+0.002), 
                   (BASE_LON+0.005, BASE_LAT+0.010), (BASE_LON-0.005, BASE_LAT+0.010)]
    },
    {
        "id": 2, "name": "Pole_Poludnie",
        "coords": [(BASE_LON-0.005, BASE_LAT-0.010), (BASE_LON+0.005, BASE_LAT-0.010), 
                   (BASE_LON+0.005, BASE_LAT-0.002), (BASE_LON-0.005, BASE_LAT-0.002)]
    },
    {
        "id": 3, "name": "Baza_Logistyczna",
        "coords": [(BASE_LON-0.002, BASE_LAT-0.002), (BASE_LON+0.002, BASE_LAT-0.002), 
                   (BASE_LON+0.002, BASE_LAT+0.002), (BASE_LON-0.002, BASE_LAT+0.002)]
    }
]

sensors_raw = [
    {"id": 101, "lon": BASE_LON + 0.001, "lat": BASE_LAT + 0.004},
    {"id": 102, "lon": BASE_LON - 0.003, "lat": BASE_LAT + 0.006},
    {"id": 103, "lon": BASE_LON + 0.002, "lat": BASE_LAT - 0.005},
    {"id": 104, "lon": BASE_LON - 0.002, "lat": BASE_LAT - 0.003},
    {"id": 105, "lon": BASE_LON, "lat": BASE_LAT}
]

waypoints = [(BASE_LAT + 0.005, BASE_LON + 0.002), (BASE_LAT + 0.006, BASE_LON - 0.003),
             (BASE_LAT - 0.006, BASE_LON + 0.002), (BASE_LAT - 0.004, BASE_LON - 0.002), (BASE_LAT, BASE_LON)]

def to_wkt_polygon(coords):
    wkt_pts = ", ".join([f"{lon} {lat}" for lon, lat in coords])
    wkt_pts += f", {coords[0][0]} {coords[0][1]}" 
    return f"POLYGON(({wkt_pts}))"

# ==============================================================================
# 3. INICJALIZACJA WYBRANEGO REPOZYTORIUM ZAPISU (DB vs CSV)
# ==============================================================================
print(f"[URUCHOMIENIE] Wybrany tryb zapisu danych: {TRYB_ZAPISU}")

conn = None
cursor = None
files_handles = []

if TRYB_ZAPISU == "DB":
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print("[DB] Połączono z PostgreSQL. Czyszczenie starych tabel...")
        cursor.execute("TRUNCATE TABLE robots_telemetry, sensors_data, geofences, sensors_static CASCADE;")
        
        # Zapis danych statycznych do DB
        for gf in geofences_raw:
            wkt = to_wkt_polygon(gf["coords"])
            cursor.execute("INSERT INTO geofences (id, name, geom_poly) VALUES (%s, %s, ST_GeomFromText(%s, 4326));", (gf["id"], gf["name"], wkt))
        for s in sensors_raw:
            cursor.execute("INSERT INTO sensors_static (sensor_id, latitude, longitude, geom_pt) VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326));", 
                           (s["id"], s["lat"], s["lon"], s["lon"], s["lat"]))
        conn.commit()
    except Exception as e:
        print(f"[BŁĄD DB] Brak połączenia z bazą danych: {e}")
        exit()
        
elif TRYB_ZAPISU == "CSV":
    print("[CSV] Tworzenie plików wyjściowych na dysku...")
    os.makedirs(config.datagen_dir, exist_ok=True)
    # Zapis danych statycznych do osobnych plików CSV
    with open(os.path.join(config.datagen_dir,"geofences.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "wkt_geometry"])
        for gf in geofences_raw: w.writerow([gf["id"], gf["name"], to_wkt_polygon(gf["coords"])])
        
    with open(os.path.join(config.datagen_dir,"sensors_static.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sensor_id", "latitude", "longitude"])
        for s in sensors_raw: w.writerow([s["id"], s["lat"], s["lon"]])
        
    # Otwarcie strumieni do plików dynamicznych
    f_telemetry = open(os.path.join(config.datagen_dir,"robots_telemetry.csv"), "w", newline="")
    f_sensors = open(os.path.join(config.datagen_dir,"sensors_data.csv"), "w", newline="")
    files_handles.extend([f_telemetry, f_sensors])
    
    writer_telemetry = csv.writer(f_telemetry)
    writer_sensors = csv.writer(f_sensors)
    
    writer_telemetry.writerow(["Id", "timestamp", "latitude", "longitude"])
    writer_sensors.writerow(["sensor_id", "timestamp", "is_threshold_exceeded"])
else:
    print("[BŁĄD] Nieprawidłowa wartość parametru TRYB_ZAPISU. Wybierz 'DB' lub 'CSV'.")
    exit()

# ==============================================================================
# 4. INICJALIZACJA STANÓW ROBOTÓW
# ==============================================================================
robots = {r_id: {
    "lat": random.choice(waypoints)[0] + random.uniform(-0.0005, 0.0005),
    "lon": random.choice(waypoints)[1] + random.uniform(-0.0005, 0.0005),
    "target_lat": random.choice(waypoints)[0], "target_lon": random.choice(waypoints)[1],
    "state": "MOVING" if r_id <= AVG_MOVING else "IDLE",
    "speed": random.uniform(0.00003, 0.00006),
    "history_lon": [], "history_lat": []
} for r_id in range(1, NUM_ROBOTS + 1)}

telemetry_buffer = []
sensor_data_buffer = []
current_time = START_TIMESTAMP
SENSOR_PERIOD = 300

# ==============================================================================
# 5. GŁÓWNA PĘTLA SYMULACJI (KROK PO KROKU 9.99 Hz)
# ==============================================================================
print(f"[SILNIK] Generowanie przebiegu...")

for step in range(TOTAL_STEPS):
    is_exceeded = 1 if (current_time % SENSOR_PERIOD) < (SENSOR_PERIOD / 2) else 0
    for s in sensors_raw:
        if TRYB_ZAPISU == "DB":
            sensor_data_buffer.append((s["id"], current_time, is_exceeded))
        else:
            sensor_data_buffer.append([s["id"], f"{current_time:.3f}", is_exceeded])
        
    moving_count = sum(1 for r in robots.values() if r["state"] == "MOVING")
    
    for r_id, r in robots.items():
        if r["state"] == "MOVING":
            dist = math.hypot(r["target_lat"] - r["lat"], r["target_lon"] - r["lon"])
            if dist < 0.0001:
                if moving_count > AVG_MOVING and random.random() < 0.4:
                    r["state"] = "IDLE"
                    moving_count -= 1
                else:
                    target = random.choice(waypoints)
                    r["target_lat"], r["target_lon"] = target[0], target[1]
            else:
                r["lat"] += (r["target_lat"] - r["lat"]) / dist * r["speed"]
                r["lon"] += (r["target_lon"] - r["lon"]) / dist * r["speed"]
        else:
            if moving_count < AVG_MOVING and random.random() < 0.1:
                r["state"] = "MOVING"
                moving_count += 1
                
        # Adaptacja struktury bufora do wybranego trybu
        if TRYB_ZAPISU == "DB":
            telemetry_buffer.append((r_id, current_time, r["lat"], r["lon"], r["lon"], r["lat"]))
        else:
            telemetry_buffer.append([r_id, f"{current_time:.3f}", f"{r['lat']:.7f}", f"{r['lon']:.7f}"])
        
        if step % 5 == 0:
            r["history_lon"].append(r["lon"])
            r["history_lat"].append(r["lat"])
            
    # Opróżnianie buforów (Zrzut okresowy co CHUNK_SIZE rekordów)
    if len(telemetry_buffer) >= CHUNK_SIZE:
        if TRYB_ZAPISU == "DB":
            execute_values(cursor, "INSERT INTO robots_telemetry (robot_id, timestamp, latitude, longitude, geom_pt) VALUES %s", 
                           telemetry_buffer, template="(%s, to_timestamp(%s), %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))")
            execute_values(cursor, "INSERT INTO sensors_data (sensor_id, timestamp, is_threshold_exceeded) VALUES %s", 
                           sensor_data_buffer, template="(%s, to_timestamp(%s), %s)")
            conn.commit()
        else:
            writer_telemetry.writerows(telemetry_buffer)
            writer_sensors.writerows(sensor_data_buffer)
            
        telemetry_buffer.clear()
        sensor_data_buffer.clear()
        print(f"   -> Zapisanio partię danych. Krok: {step}/{TOTAL_STEPS}")

    current_time += DT

# Czyszczenie pozostałości w buforach na koniec symulacji
if telemetry_buffer:
    if TRYB_ZAPISU == "DB":
        execute_values(cursor, "INSERT INTO robots_telemetry (robot_id, timestamp, latitude, longitude, geom_pt) VALUES %s", telemetry_buffer, template="(%s, to_timestamp(%s), %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))")
        execute_values(cursor, "INSERT INTO sensors_data (sensor_id, timestamp, is_threshold_exceeded) VALUES %s", sensor_data_buffer, template="(%s, to_timestamp(%s), %s)")
        conn.commit()
    else:
        writer_telemetry.writerows(telemetry_buffer)
        writer_sensors.writerows(sensor_data_buffer)

# Zamykanie otwartych połączeń / plików
if TRYB_ZAPISU == "DB":
    cursor.close()
    conn.close()
    print("[ZAKOŃCZONO] Dane zostały wgrane bezpośrednio do bazy Postgres.")
else:
    for handle in files_handles:
        handle.close()
    print("[ZAKOŃCZONO] Dane zostały zapisane do plików CSV.")

# ==============================================================================
# 6. RYSOWANIE OBRAZU OPERACYJNEGO (Niezależne od wybranego trybu zapisu)
# ==============================================================================
print("[WIZUALIZACJA] Generowanie mapy tras i obiektów...")
fig, ax = plt.subplots(figsize=(11, 9))

for gf in geofences_raw:
    poly_patch = patches.Polygon(gf["coords"], closed=True, linewidth=2, edgecolor='red', facecolor='red', alpha=0.15, label="Geofence" if gf["id"]==1 else "")
    ax.add_patch(poly_patch)
    mid_lon = sum(p[0] for p in gf["coords"]) / len(gf["coords"])
    mid_lat = sum(p[1] for p in gf["coords"]) / len(gf["coords"])
    ax.text(mid_lon, mid_lat, gf["name"], color='darkred', fontsize=10, weight='bold', ha='center')

s_lons = [s["lon"] for s in sensors_raw]
s_lats = [s["lat"] for s in sensors_raw]
ax.scatter(s_lons, s_lats, color='blue', marker='^', s=100, zorder=4, label="Czujnik Środowiskowy")
for s in sensors_raw:
    ax.text(s["lon"]+0.0002, s["lat"], f"ID:{s['id']}", color='blue', fontsize=8, va='center')

for r_id, r in robots.items():
    if r["history_lon"]:
        ax.plot(r["history_lon"], r["history_lat"], linewidth=1.2, alpha=0.6, label="Trasy Robotów" if r_id==1 else "")
        ax.scatter(r["history_lon"][-1], r["history_lat"][-1], s=25, edgecolor='black', zorder=5)

ax.set_title(f"Mapa Świata Operacyjnego (Tryb wyjściowy: {TRYB_ZAPISU})\nPolygony Geofence, Lokalizacje Czujników oraz Trajektorie Robotów", fontsize=12, weight='bold')
ax.set_xlabel("Długość geograficzna (Longitude)")
ax.set_ylabel("Szerokość geograficzna (Latitude)")
ax.grid(True, linestyle='--', alpha=0.4)
ax.legend(loc="upper right")
ax.set_aspect('equal', 'datalim')

output_image = os.path.join(config.datagen_dir,"syntetyczna_mapa_operacyjna.png")
plt.tight_layout()
plt.savefig(output_image, dpi=200)
plt.close()
print(f"[WIZUALIZACJA] Wykres zapisano pomyślnie jako: '{output_image}'")