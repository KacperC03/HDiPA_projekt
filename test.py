from mcap.reader import make_reader
import config
# Wklej tutaj swoją prawdziwą ścieżkę do pliku
mcap_file_path = config.file_path

with open(mcap_file_path, "rb") as f:
    reader = make_reader(f)
    
    # Pobieramy podsumowanie pliku MCAP
    summary = reader.get_summary()
    
    if summary and summary.channels:
        # Wyciągamy tematy ze słownika kanałów
        topics = set(channel.topic for channel in summary.channels.values())
    else:
        # Rezerwowy plan: jeśli plik nie ma wygenerowanego podsumowania,
        # musimy przeskanować wiadomości (dla dużych plików to może chwilę potrwać)
        print("Brak indeksu podsumowania. Skanuję zawartość wiadomości...")
        topics = set()
        for schema, channel, message in reader.iter_messages():
            topics.add(channel.topic)

print("\nTematy (topics) znalezione w Twoim pliku .mcap:")
for t in sorted(topics):
    print(f" - {t}")