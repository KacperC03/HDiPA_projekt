import os
import pandas as pd
import matplotlib.pyplot as plt
from mcap_ros2.reader import read_ros2_messages
import config
from mcap.reader import make_reader
from mcap_ros2.decoder import DecoderFactory
# ==============================================================================
# KONFIGURACJA
# ==============================================================================
mcap_file_path = config.file_path
output_dir = "raports"
os.makedirs(output_dir, exist_ok=True)


# ==============================================================================
# FUNKCJE POMOCNICZE DO SPŁASZCZANIA STRUKTUR
# ==============================================================================
def inspect_and_flatten_struct(obj, prefix="", schema_lines=None, current_row=None):
    """
    Rekurencyjnie przechodzi przez obiekt wiadomości ROS2.
    - Jeśli schema_lines nie jest None: buduje tekstowe drzewo struktury.
    - Jeśli current_row nie jest None: wyciąga wartości numeryczne do słownika.
    """
    attributes = [a for a in dir(obj) if not a.startswith('_') and a not in ['get_fields_and_field_types', 'SLOT_TYPES']]
    
    for attr in attributes:
        val = getattr(obj, attr)
        field_name = f"{prefix}.{attr}" if prefix else attr
        
        # Przypadek 1: Wartość numeryczna (int, float)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            if schema_lines is not None:
                schema_lines.append(f"{field_name} (Wartość numeryczna)")
            if current_row is not None:
                current_row[field_name] = val
                
        # Przypadek 2: Złożona struktura (kolejny obiekt/wiadomość podrzędna)
        elif hasattr(val, '__dict__') or (type(val).__name__ in ['Time', 'Duration', 'Vector3', 'Point', 'Quaternion']):
            if schema_lines is not None:
                schema_lines.append(f"{field_name} [Struktura: {type(val).__name__}]")
            inspect_and_flatten_struct(val, prefix=field_name, schema_lines=schema_lines, current_row=current_row)
            
        # Przypadek 3: Tablice / Listy (np. macierze kowariancji)
        elif isinstance(val, (list, tuple)):
            if schema_lines is not None:
                schema_lines.append(f"{field_name} [Tablica/Lista o długości {len(val)}]")
            # Wyciągamy elementy tablicy jako osobne kolumny (np. status.covariance.0, status.covariance.1)
            for i, item in enumerate(val):
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    if current_row is not None:
                        current_row[f"{field_name}.{i}"] = item
        
        # Przypadek 4: Inne (stringi, boole) - zapisujemy strukturę, pomijamy w profilowaniu numerycznym
        else:
            if schema_lines is not None:
                schema_lines.append(f"{field_name} ({type(val).__name__})")


# ==============================================================================
# JEDNOPRZEBIEGOWE WCZYTYWANIE I PARSOWANIE PLIKU
# ==============================================================================
data_by_topic = {}
schemas_by_topic = {}

print("Rozpoczynam zaawansowane profilowanie pliku MCAP...")
print("Skanowanie wiadomości (jednorazowy przebieg z pomijaniem nieznanych typów)...")

total_processed = 0
total_decoded = 0

with open(mcap_file_path, "rb") as f:
    reader = make_reader(f)
    ros2_decoder_factory = DecoderFactory()
    
    for schema, channel, message in reader.iter_messages():
        total_processed += 1
        topic = channel.topic
        
        if not schema:
            continue
            
        try:
            decoder = ros2_decoder_factory.decoder_for(channel.message_encoding, schema)
            if decoder is None:
                continue
                
            ros_msg = decoder(message.data)
            total_decoded += 1
        except Exception:
            continue
        
        # Logika parsowania struktury
        if topic not in schemas_by_topic:
            schema_lines = []
            inspect_and_flatten_struct(ros_msg, schema_lines=schema_lines)
            schemas_by_topic[topic] = schema_lines
            data_by_topic[topic] = []
            
        # Bezpieczny timestamp bezpośrednio z metadanych MCAP
        current_row = {'_msg_timestamp': message.log_time / 1e9}
        inspect_and_flatten_struct(ros_msg, current_row=current_row)
        data_by_topic[topic].append(current_row)

print(f"Przeanalizowano komunikatów: {total_processed}")
print(f"Poprawnie zdekodowano (ROS2): {total_decoded}")


# ==============================================================================
# GENEROWANIE RAPORTÓW I HISTOGRAMÓW
# ==============================================================================
print("\nAnaliza zebranych danych i generowanie wykresów...")

for topic, records in data_by_topic.items():
    safe_topic_name = topic.replace('/', '_').strip('_')
    print(f"\n==================================================")
    print(f"TEMAT: {topic}")
    print(f"==================================================")
    
    # 1. Wypisanie i zapisanie struktury drzewiastej
    print("Struktura komunikatu:")
    structure_text = f"STRUKTURA TEMATU: {topic}\n" + "-"*50 + "\n"
    for line in schemas_by_topic[topic]:
        print(f"  └── {line}")
        structure_text += f"  └── {line}\n"
        
    with open(os.path.join(output_dir, f"struktura_{safe_topic_name}.txt"), "w", encoding="utf-8") as f_schema:
        f_schema.write(structure_text)

    if not records:
        print("  --> Brak rekordów do profilowania.")
        continue
        
    # 2. Tworzenie DataFrame i obliczanie statystyk
    df = pd.DataFrame(records)
    
    # Obliczanie różnic czasowych dla analizy częstotliwości (dt)
    df['_msg_dt'] = df['_msg_timestamp'].diff()
    
    cols_to_profile = [c for c in df.columns if c != '_msg_timestamp']
    
    if len(cols_to_profile) <= 1: # Jeśli jest tylko _msg_dt
        print("  --> Brak wykrytych pól numerycznych do profilowania statystycznego.")
        continue
        
    # Wyznaczenie miar statystycznych (średnia, mediana, braki danych)
    stats = df[cols_to_profile].describe().loc[['count', 'mean', 'std', 'min', '50%', 'max']]
    stats.rename(index={'50%': 'median'}, inplace=True)
    
    # Dodanie informacji o brakach danych (NaN)
    missing_row = pd.Series(df[cols_to_profile].isna().sum(), name='missing_count')
    stats = pd.concat([stats, missing_row.to_frame().T])
    
    print("\nStatystyki opisowe parametrów:")
    print(stats.to_string())
    
    # Zapis statystyk do CSV
    stats.to_csv(os.path.join(output_dir, f"statystyki_{safe_topic_name}.csv"))
    
    # 3. Generowanie histogramów dla profilowanych pól
    for col in cols_to_profile:
        clean_data = df[col].dropna()
        if clean_data.empty:
            continue
            
        plt.figure(figsize=(7, 4))
        clean_data.hist(bins=25, color='#1e3a8a', edgecolor='black', alpha=0.7)
        
        title_name = "Interwał nadawania (dt)" if col == '_msg_dt' else col
        plt.title(f"Temat: {topic}\nParametr: {title_name}")
        plt.xlabel("Wartość")
        plt.ylabel("Liczba próbek")
        plt.grid(True, linestyle='--', alpha=0.5)
        plt.tight_layout()
        
        # Zapis wykresu
        plt.savefig(os.path.join(output_dir, f"hist_{safe_topic_name}_{col.replace('.', '_')}.png"))
        plt.close()

print(f"\n[SUKCES] Profilowanie zakończone. Wyniki znajdziesz w folderze: '{output_dir}'")