import os
import pandas as pd
import matplotlib.pyplot as plt
from mcap_ros2.reader import read_ros2_messages
import config

mcap_file_path = config.file_path
topic_name = config.topic_name

records = []

print("Rozpoczynam proces profilowania pliku MCAP...")
print(f"Szukam danych w temacie: {topic_name}")

if not os.path.exists(mcap_file_path):
    print(f"\n[BŁĄD] Nie znaleziono pliku pod ścieżką: {mcap_file_path}")
    print("Upewnij się, że plik znajduje się w tym folderze i poprawnie wpisałeś jego nazwę.")
    exit()

# 2. Odczyt i dekodowanie binarnych danych ROS2
with open(mcap_file_path, "rb") as f:
    # read_ros2_messages automatycznie dekoduje strukturę NavSatFix
    for msg in read_ros2_messages(f, topics=[topic_name]):
        ros_msg = msg.ros_msg
        
        # Konwersja czasu systemowego robota (sekundy + nanosekundy) do formatu UNIX
        timestamp = ros_msg.header.stamp.sec + (ros_msg.header.stamp.nanosec / 1e9)
        
        # Pobranie współrzędnych
        lat = ros_msg.latitude
        lon = ros_msg.longitude
        
        # SPRYTNY TRYK DLA PROFILOWANIA:
        # W robotyce, jeśli GPS straci sygnał, często zamiast "błędu" wpisuje współrzędne 0.0.
        # Ponieważ Twój robot działa w Polsce, wartości 0.0 oznaczają w praktyce brak danych.
        if lat == 0.0 and lon == 0.0:
            lat = None
            lon = None
            
        records.append({
            "Id": 1,  # Stały numer porządkowy robota
            "timestamp": timestamp,
            "latitude": lat,
            "longitude": lon,
            "gps_status": ros_msg.status.status # -1 = brak fix, 0 = fix, 1/2 = zaawansowany fix (RTK)
        })

if not records:
    print(f"\n[BŁĄD] Skrypt nie znalazł żadnych wiadomości w temacie {topic_name}.")
    print("Sprawdź czy w pliku na pewno są dane z tego czujnika.")
    exit()

# 3. Tworzenie tabeli Pandas
df = pd.DataFrame(records)

# Różnice czasowe między ramkami (do analizy częstotliwości próbkowania)
df['dt'] = df['timestamp'].diff()

# 4. WYGENEROWANIE RAPORTU TEKSTOWEGO DO TERMINALA
print("\n" + "="*50)
print("         RAPORT PROFILOWANIA ZBIORU DANYCH")
print("="*50)

print(f"\n1. CO OPISUJE ZBIÓR:")
print(f"   - Dane telemetryczne pozycji geograficznej robota ID: 1")
print(f"   - Całkowita liczba zarejestrowanych ramek (rekordów): {len(df)}")
print(f"   - Zakres czasu w logu: od {df['timestamp'].min():.3f}s do {df['timestamp'].max():.3f}s")
print(f"   - Całkowity czas trwania nagrania: {df['timestamp'].max() - df['timestamp'].min():.2f} sekund")

print(f"\n2. ANALIZA BRAKUJĄCYCH WARTOŚCI:")
braki_lat = df['latitude'].isna().sum()
braki_lon = df['longitude'].isna().sum()
braki_sygnalu = (df['gps_status'] == -1).sum()

print(f"   - Puste/Błędne szerokości (latitude): {braki_lat} z {len(df)} ramek ({(braki_lat/len(df))*100:.2f}%)")
print(f"   - Puste/Błędne długości (longitude): {braki_lon} z {len(df)} ramek ({(braki_lon/len(df))*100:.2f}%)")
print(f"   - Liczba ramek, w których czujnik zgłosił całkowity brak sygnału (status=-1): {braki_sygnalu}")

print(f"\n3. STATYSTYKI OPISOWE WARTOŚCI NUMERYCZNYCH:")
# Wyświetlamy najważniejsze miary: średnia, mediana (50%), min, max, odchylenie std.
statystyki = df[['latitude', 'longitude', 'dt']].describe().loc[['count', 'mean', 'std', 'min', '50%', 'max']]
statystyki.rename(index={'50%': 'median (mediana)'}, inplace=True)
print(statystyki.to_string())

# 5. GENEROWANIE WYKRESÓW (HISTOGRAMU I TRASY)
print("\n" + "="*50)
print("Generowanie wykresów do opracowania...")

# Wykres A: Histogram stabilności próbkowania czasu (dt)
plt.figure(figsize=(9, 4))
plt.hist(df['dt'].dropna(), bins=25, color='#3b82f6', edgecolor='black', alpha=0.7)
plt.title('Histogram interwałów próbkowania (dt pomiędzy wiadomościami)')
plt.xlabel('Interwał czasu [sekundy]')
plt.ylabel('Liczba próbek')
plt.grid(True, linestyle='--', alpha=0.5)
plt.tight_layout()
plt.savefig('histogram_probkowania.png')
print("   [SUKCES] Zapisano wykres: histogram_probkowania.png")

# Wykres B: Wizualizacja ścieżki (Trasa z GPS)
plt.figure(figsize=(7, 6))
df_valid = df.dropna(subset=['latitude', 'longitude'])
if not df_valid.empty:
    plt.plot(df_valid['longitude'], df_valid['latitude'], color='#0f172a', linewidth=1.5, label='Ścieżka robota')
    plt.scatter(df_valid['longitude'].iloc[0], df_valid['latitude'].iloc[0], color='green', s=60, label='Start', zorder=5)
    plt.scatter(df_valid['longitude'].iloc[-1], df_valid['latitude'].iloc[-1], color='red', s=60, label='Koniec', zorder=5)
plt.title('Profil przestrzenny - Wyznaczona trasa przejazdu')
plt.xlabel('Długość geograficzna (Longitude)')
plt.ylabel('Szerokość geograficzna (Latitude)')
plt.grid(True, linestyle='--', alpha=0.5)
plt.legend()
plt.tight_layout()
plt.savefig('wykres_trasy.png')
print("   [SUKCES] Zapisano wykres: wykres_trasy.png")

print("\nProfilowanie zakończone! Wszystkie dane i wykresy są gotowe do skopiowania do Twojego dokumentu.")