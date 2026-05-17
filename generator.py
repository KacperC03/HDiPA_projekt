import csv
import math
import random
import os
import config
# ==============================================================================
# PARAMETRY GENERATORA (Możesz je dowolnie zwiększać do testów wydajnościowych)
# ==============================================================================
NUM_ROBOTS = config.robot_num          
AVG_MOVING = config.avg_move         
DURATION_MINUTES = config.duration
START_TIMESTAMP = config.start_timestamp

SAMPLING_RATE = config.sampling_rate
DT = 1.0 / SAMPLING_RATE # Krok czasowy
TOTAL_STEPS = int(DURATION_MINUTES * 60 * SAMPLING_RATE)

BASE_LAT = config.lat
BASE_LON = config.lon
DATA_DIR = config.datagen_dir
print(f"Generowanie danych dla {NUM_ROBOTS} robotów (średnio {AVG_MOVING} w ruchu)...")
print(f"Liczba kroków czasowych: {TOTAL_STEPS} przy {SAMPLING_RATE} Hz. Łącznie {TOTAL_STEPS * NUM_ROBOTS} rekordów.")

# ==============================================================================
# GENEROWANIE OTOCZENIA STATYCZNEGO (Geofence i Czujniki)
# ==============================================================================
# Definiujemy 3 strefy (Geofences) jako wielokąty WKT
geofences = [
    {"id": 1, "name": "Pole_Polnoc", "wkt": f"POLYGON(({BASE_LON-0.005} {BASE_LAT+0.002}, {BASE_LON+0.005} {BASE_LAT+0.002}, {BASE_LON+0.005} {BASE_LAT+0.010}, {BASE_LON-0.005} {BASE_LAT+0.010}, {BASE_LON-0.005} {BASE_LAT+0.002}))"},
    {"id": 2, "name": "Pole_Poludnie", "wkt": f"POLYGON(({BASE_LON-0.005} {BASE_LAT-0.010}, {BASE_LON+0.005} {BASE_LAT-0.010}, {BASE_LON+0.005} {BASE_LAT-0.002}, {BASE_LON-0.005} {BASE_LAT-0.002}, {BASE_LON-0.005} {BASE_LAT-0.010}))"},
    {"id": 3, "name": "Baza_Logistyczna", "wkt": f"POLYGON(({BASE_LON-0.002} {BASE_LAT-0.002}, {BASE_LON+0.002} {BASE_LAT-0.002}, {BASE_LON+0.002} {BASE_LAT+0.002}, {BASE_LON-0.002} {BASE_LAT+0.002}, {BASE_LON-0.002} {BASE_LAT-0.002}))"}
]

# Definiujemy 5 czujników statycznych (Proximity) rozrzuconych w terenie
sensors = [
    {"id": 101, "lon": BASE_LON + 0.001, "lat": BASE_LAT + 0.004},
    {"id": 102, "lon": BASE_LON - 0.003, "lat": BASE_LAT + 0.006},
    {"id": 103, "lon": BASE_LON + 0.002, "lat": BASE_LAT - 0.005},
    {"id": 104, "lon": BASE_LON - 0.002, "lat": BASE_LAT - 0.003},
    {"id": 105, "lon": BASE_LON, "lat": BASE_LAT} # W samym centrum bazy
]

# Zapis struktur statycznych
fences_file = os.path.join(DATA_DIR,"geofences.csv")
with open(fences_file, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["id", "name", "wkt_geometry"])
    for gf in geofences: writer.writerow([gf["id"], gf["name"], gf["wkt"]])

sensors_pos_file = os.path.join(DATA_DIR,"sensors_static.csv")
with open(sensors_pos_file, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["sensor_id", "latitude", "longitude"])
    for s in sensors: writer.writerow([s["id"], s["lat"], s["lon"]])

# ==============================================================================
# INICJALIZACJA STANÓW ROBOTÓW
# ==============================================================================
# Wspólne punkty docelowe (Waypoints), do których zmierzają roboty (wymusza kolizje i bliskość)
waypoints = [
    (BASE_LAT + 0.005, BASE_LON + 0.002),
    (BASE_LAT + 0.006, BASE_LON - 0.003),
    (BASE_LAT - 0.006, BASE_LON + 0.002),
    (BASE_LAT - 0.004, BASE_LON - 0.002),
    (BASE_LAT, BASE_LON)
]

robots = {}
for r_id in range(1, NUM_ROBOTS + 1):
    start_pt = random.choice(waypoints)
    robots[r_id] = {
        "lat": start_pt[0] + random.uniform(-0.0005, 0.0005),
        "lon": start_pt[1] + random.uniform(-0.0005, 0.0005),
        "target_lat": start_pt[0],
        "target_lon": start_pt[1],
        "state": "MOVING" if r_id <= AVG_MOVING else "IDLE",
        "speed": random.uniform(0.00002, 0.00005) # prędkość w stopniach na krok
    }

# ==============================================================================
# GŁÓWNA PĘTLA SYMULACJI (Generowanie serii czasowych)
# ==============================================================================
telemetry_file = os.path.join(DATA_DIR,"robots_telemetry.csv")
sensors_data_file = os.path.join(DATA_DIR,"sensors_data.csv")

f_telemetry = open(telemetry_file, "w", newline="")
f_sensors = open(sensors_data_file, "w", newline="")

writer_telemetry = csv.writer(f_telemetry)
writer_sensors = csv.writer(f_sensors)

# Nagłówki plików CSV
writer_telemetry.writerow(["Id", "timestamp", "latitude", "longitude"])
writer_sensors.writerow(["sensor_id", "timestamp", "is_threshold_exceeded"])

current_time = START_TIMESTAMP

# Cykl dla czujników (częstotliwość zmian progowych - np. zmiana stanu co 5 minut)
SENSOR_PERIOD = 300 

for step in range(TOTAL_STEPS):
    # 1. Zapis stanu czujników (Dokładnie przez 50% czasu mają wartość przekroczoną)
    # Sprawdzamy czy aktualna faza czasu mieści się w pierwszej połowie okresu SENSOR_PERIOD
    is_exceeded = 1 if (current_time % SENSOR_PERIOD) < (SENSOR_PERIOD / 2) else 0
    for s in sensors:
        writer_sensors.writerow([s["id"], f"{current_time:.3f}", is_exceeded])
        
    # 2. Zarządzanie ruchem robotów (Prawdopodobieństwo zmiany stanu dla utrzymania średniej)
    moving_count = sum(1 for r in robots.values() if r["state"] == "MOVING")
    
    # 3. Aktualizacja pozycji i zapis telemetrii robotów
    for r_id, r in robots.items():
        # Maszyna stanów zmieniająca cele
        if r["state"] == "MOVING":
            dist_to_target = math.hypot(r["target_lat"] - r["lat"], r["target_lon"] - r["lon"])
            
            if dist_to_target < 0.0001: # Robot dotarł do celu
                # Jeśli jest za dużo robotów w ruchu, idź odpocząć (IDLE)
                if moving_count > AVG_MOVING and random.random() < 0.5:
                    r["state"] = "IDLE"
                    moving_count -= 1
                else:
                    # Wylosuj nowy cel podróży
                    new_target = random.choice(waypoints)
                    r["target_lat"], r["target_lon"] = new_target[0], new_target[1]
            else:
                # Ruch w stronę celu
                step_lat = (r["target_lat"] - r["lat"]) / dist_to_target * r["speed"]
                step_lon = (r["target_lon"] - r["lon"]) / dist_to_target * r["speed"]
                r["lat"] += step_lat
                r["lon"] += step_lon
        else:
            # Stan IDLE: Robot stoi w miejscu, ale dla testów kompresji TimescaleDB 
            # nadal generujemy rekord z identyczną pozycją z częstotliwością 9.99 Hz
            if moving_count < AVG_MOVING and random.random() < 0.1:
                r["state"] = "MOVING"
                moving_count += 1

        # ZAPIS ZGODNY Z TWÓIM WZOREM Z PLIKU
        writer_telemetry.writerow([r_id, f"{current_time:.3f}", f"{r['lat']:.7f}", f"{r['lon']:.7f}"])
        
    current_time += DT

f_telemetry.close()
f_sensors.close()
print("Sukces! Wygenerowano pliki: robots_telemetry.csv, sensors_data.csv, geofences.csv, sensors_static.csv")