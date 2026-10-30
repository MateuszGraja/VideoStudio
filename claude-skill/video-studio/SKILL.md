---
name: video-studio
description: Montaż filmów z surowego materiału przy pomocy lokalnego narzędzia `vs` (~/video-studio, ffmpeg + NVENC) z panelem podglądu na http://127.0.0.1:8765. Używaj, gdy użytkownik chce zmontować film, dodać materiał, poprawić montaż, wyrenderować wersję lub przejrzeć uwagi z panelu.
---

# Video Studio — montaż z Claude Code

Narzędzie: katalog repozytorium VideoStudio (domyślnie `~/video-studio`; komenda `vs`, Python w `.venv` repo). Materiał źródłowy NIE jest kopiowany —
projekt trzyma ścieżki do oryginałów. Wszystko o projekcie leży w `~/video-studio/projects/<nazwa>/`.

Użytkownik nie umie montować i chce minimum pracy: Ty podejmujesz decyzje montażowe, on ogląda w panelu i zgłasza uwagi.
Rozmawiaj po polsku.

## 0. Panel — zawsze najpierw
```bash
curl -s localhost:8765/api/projects >/dev/null || echo "panel off"
```
Użytkownik uruchamia panel skrótem z pulpitu (`start.sh` w repo, opcja `--claude` = terminal z `claude --continue`
w folderze materiału aktywnego projektu). Panel na starcie pokazuje baner „Ostatni stan” (`/api/p/<n>/summary`).
Jeśli nie działa: uruchom `vs serve` w tle (Bash `run_in_background: true`) i podaj użytkownikowi link http://127.0.0.1:8765.
Przez całą pracę wysyłaj krótkie komunikaty do panelu: `vs note "Opisuję klipy z lipca (12/37)…"` — to jedyny sposób,
żeby użytkownik widział, co robisz między komendami. Rób to przy każdym nowym etapie.

## 1. Materiał
```bash
vs projects                      # lista, * = aktywny
vs new <nazwa> --source <folder> # albo: vs use <nazwa>
vs ingest [--add <folder>]       # metadane, miniatury, arkusze klatek (przyrostowo)
vs analyze                       # ostrość / ekspozycja / drgania / cięcia + "najlepsze" okna 4 s
vs list --best                   # przegląd: data, długość, jakość q, flagi, najlepsze fragmenty
```
Data nagrania pochodzi z metadanych (fallback: nazwa pliku, mtime).

**Struktura folderów = struktura opowieści.** Każdy klip ma `group` (folder 1. poziomu) i `event` (podfolder, np.
„2026_08_10 - loty tragedia”; `event_label` = opis bez daty). `vs list` grupuje po group/event; `vs list --group TEAM`.
Grupy użytkownika:
- `DEVELOPMENT` — budowa projektu w Polsce, podfoldery chronologiczne z datą i opisem (oblot, testy autonomii, ML,
  modelarnia, „loty tragedia” = awaria/rozbicie — to emocjonalny punkt zwrotny, pokaż go, potem powrót do pracy).
- `COMPETITIONS` — wyjazdy na zawody, podfoldery typu „09_10 - 09_22 SUAS”, „10_04 - 10_17 CUADC” (zakres dat + nazwa).
  Zawierają też zwiedzanie po drodze — rozdziel w opisach/tagach (`tags: ["zawody"]` vs `["zwiedzanie"]`).
- `TEAM` — zdjęcia drużyny: plansza „Zespół” po intro (seria zdjęć 2–3 s, `kenburns`, xfade) i/lub tło napisów końcowych.
Jeśli projekt ma `projects/<n>/tools/build_edl.py` (generator EDL z listy ujęć), edytuj ten skrypt i uruchamiaj go zamiast
ręcznie zmieniać edl.json; napisy dni robi on automatycznie (słownik DAY_TEXT z nazw folderów; pierwszy kadr każdego dnia).
Nazwa podfolderu to najlepsze źródło napisu (`caption.label` = data, `caption.text` = event_label, poprawiony po polsku,
np. „loty tragedia” → „Dzień, w którym wszystko poszło nie tak”). Przeniesione pliki są automatycznie podpinane przy
`vs ingest` (opisy zostają); klipy, których nie ma na dysku, mają `missing: true`.

## 2. Oglądanie i opisywanie klipów
Każdy klip ma arkusz klatek z timestampami: `projects/<n>/sheets/<id>.jpg` — przeczytaj go narzędziem Read (obraz).
Szczegóły fragmentu: `vs sheet <id> --start 20 --end 35 -n 12` (zwraca ścieżkę JPG). Ocena sekunda po sekundzie: `vs info <id>`.

Po obejrzeniu partii (np. jednego dnia) zapisz opisy hurtowo:
```bash
cat > /tmp/.../tags.json <<'EOF'
{"c0003": {"desc": "Przygotowanie płatowca na pasie, start z ręki", "chapter": 2, "rating": 4, "tags": ["teren","start"]}}
EOF
vs tagjson /tmp/.../tags.json
```
`rating` 1–5 = przydatność do filmu (5 = musi być). `chapter` = numer rozdziału z `vs chapters`.
Rozdziały projektu zapisz raz: `vs chapters --set '["Praca przy komputerze","Symulacje",...]'`.
Opisz WSZYSTKIE klipy zanim zaczniesz montować — od tego zależy jakość wyboru.

## 3. Muzyka
Użytkownik dodaje muzykę w panelu (zakładka „Muzyka”: przeciągnij pliki albo bezpośredni link do pliku audio) i może
wpisać przy utworze „nastrój / gdzie użyć”. `vs music` → lista utworów z BPM, długością i tymi uwagami — traktuj je jako
wskazówki do przydziału utworów do rozdziałów. Pliki leżą w `projects/<n>/music/` (w EDL wystarczy sama nazwa pliku).
Pobierania ze Spotify/YouTube/innych streamingów NIE robimy (chronione utwory, łamie regulamin; na YouTube film dostałby
blokadę/wyciszenie) — proponuj Pixabay Music, YouTube Audio Library, Free Music Archive.
`vs beats <plik> [--bars]` → BPM, początki taktów, mocne akcenty, profil energii.
Cięcia ustawiaj na początkach taktów (`bars`) — to najmocniej podnosi „profesjonalny” odbiór. Analiza używa Beat This!
(beaty + downbeaty, GPU; librosa tylko jako zapas — myliła tempo x2). `vs beats <plik> --force` = ponowna analiza.
Długość ujęcia = całe takty (wyjątkowo pół taktu). Weryfikacja: `scdet` na renderze vs takty (cel: ±1 klatka).
Zdjęcia: `style: card` (ramka na rozmytym tle, bez zoomu — użytkownik NIE lubi kenburns), `style: person` + `name`/`role` dla zespołu.

## 4. Montaż = plik `projects/<n>/edl.json`
```json
{
  "title": "Tytuł filmu", "version": 1,
  "settings": {"look": "warm", "clip_volume": 0.0, "transition_duration": 0.6, "accent": "#FFC440"},
  "timeline": [
    {"type": "title", "style": "main", "label": "Projekt 2026", "text": "Tytuł", "subtitle": "Podtytuł",
     "duration": 5, "bg": {"id": "c0002", "in": 5}},
    {"type": "title", "style": "chapter", "label": "Rozdział 1", "text": "Symulacje", "duration": 3.5, "transition": "fade"},
    {"type": "clip", "id": "c0003", "in": 22.0, "out": 26.0, "transition": "fade",
     "caption": {"label": "2 lipca 2026 · lotnisko", "text": "Testy lądowań autonomicznych"}, "note": "dlaczego to ujęcie"},
    {"type": "clip", "id": "c0002", "in": 1, "out": 4, "transition": "xfade"},
    {"type": "clip", "id": "c0018", "in": 2, "out": 8, "speed": 2, "stabilize": true},
    {"type": "image", "id": "c0040", "duration": 4, "kenburns": "in"},
    {"type": "title", "style": "end", "text": "Dziękujemy", "subtitle": "Zespół · 2026", "duration": 5, "transition": "fade"}
  ],
  "music": [
    {"file": "track1.mp3", "volume": 0.9},
    {"file": "track2.mp3", "at": 245.0, "in": 12.0, "fade_in": 3, "fade_out": 3}
  ]
}
```
Pola elementu:
- `transition` (jak element WCHODZI po poprzednim): `cut` (domyślne) · `fade` (przez czerń, między rozdziałami) ·
  `xfade` (przenikanie) · nazwa xfade: `slideleft`, `wipeleft`, `circleopen`, `smoothleft`, `fadewhite`, `zoomin`…
  `transition_duration` nadpisuje domyślną.
- clip: `id`, `in`, `out` (sekundy w źródle), `speed` (2–8 = timelapse; <1 zwolnienie, sensowne tylko dla 60/120 fps),
  `stabilize` (vidstab, dla trzęsących się ujęć — wolniejsze), `fit`: `auto|cover|contain|blur` (pion → auto daje rozmyte tło),
  `volume` (oryginalny dźwięk, domyślnie `settings.clip_volume`), `look` (nadpisanie).
- `caption`: string albo `{text, label, start, duration, position: bottom-left|bottom-center|top-left}`.
- title: `style` `main|chapter|end`, `text`, `subtitle`, `label`, `duration`,
  `bg`: `"#101014"` albo `{"id": klip, "in": s, "blur": 10, "dim": 0.45}` (rozmyty ruchomy klip w tle — wygląda najlepiej).
- `chapter_marker`: dodaje rozdział YouTube na dowolnym elemencie (plansze `chapter` dodają się same).
- settings: `look` `none|warm|cool|punchy|film|bw`, `loudness` (-14 LUFS), `music_crossfade`, `end_fade`, `width/height/fps`.
- music: kolejne utwory domyślnie idą jeden po drugim z crossfade; `at` = start na osi czasu filmu, `in`/`out` = fragment pliku.

## 5. Zasady montażu (film dokumentalny/podsumowanie projektu, bez narracji)
- Opowiadaj chronologicznie rozdziałami; każdy rozdział: plansza `chapter` (3–4 s, `fade`) → ujęcie ustalające (szeroki plan
  miejsca) z napisem `caption` (data · miejsce + 3–6 słów co się dzieje) → sekwencja → mocny moment (lot, sukces, porażka).
- Długości ujęć: 2–4 s w dynamicznych sekwencjach (cięcia na takty), 4–7 s dla krajobrazów/zwiedzania, max ~10 s
  dla kluczowego momentu (np. całe lądowanie). Rzadko pokazuj ten sam klip dwa razy.
- Wybieraj fragmenty z `best` i wysokim `rating`; flagi `trzęsie się` → inny fragment albo `stabilize: true`;
  `ciemne`/`nieostre` → unikaj. Długie statyczne sceny (praca przy komputerze) → `speed: 4–8` jako timelapse.
- Głównie cięcia `cut` na beat; `xfade` oszczędnie (zmiana miejsca/czasu w obrębie rozdziału); `fade` tylko między rozdziałami.
- Jeden `look` na cały film. Napisy krótkie, spójne (format label: „7 września 2026 · Nazwa miejsca”).
- Muzyka zmienia się z nastrojem rozdziałów (spokojna praca przy komputerze → energiczne testy → kulminacja na zawodach →
  lżejsza przy zwiedzaniu → wyciszenie na napisach końcowych). Wejścia nowych utworów ustawiaj na planszach rozdziałów.
- W `note` przy elemencie zapisuj krótko powód wyboru — użytkownik widzi to w panelu.

## 6. Sprawdzanie i render
```bash
vs timeline               # tabela z czasami startu, rozdziały, błędy/ostrzeżenia (exit 1 przy błędzie)
vs still <idx>            # klatka z elementu → JPG; obejrzyj Read-em (sprawdzaj plansze i napisy!)
vs render --items 10-25   # szybki podgląd fragmentu 540p (cache: zmieniane są tylko zmienione ujęcia)
vs render                 # podgląd całości 540p
vs render --final         # 1080p, dwuprzebiegowa normalizacja głośności, rozdziały w MP4 + .chapters.txt dla YouTube
```
Rendery trwają długo — uruchamiaj je w tle (`run_in_background`) i nie odpytuj w pętli; panel pokazuje postęp na żywo.
Po renderze sprawdź kilka klatek (`ffmpeg -ss T -i plik -frames:v 1 x.jpg` + Read) zanim powiesz użytkownikowi „gotowe”.

## 7. Uwagi użytkownika
Użytkownik pisze uwagi w panelu (do konkretnego ujęcia, klipu albo momentu filmu). Przed każdą nową iteracją:
```bash
vs feedback               # lista otwartych uwag z kontekstem
vs feedback --resolve 3 4 # po wprowadzeniu (albo: --resolve all)
```
Po zmianach podbij `"version"` w edl.json, wyrenderuj podgląd zmienionego fragmentu i daj znać przez `vs note`.

## Rozwiązywanie problemów
- Log ffmpeg: `projects/<n>/cache/ffmpeg.log`; stan zadania: `vs status`.
- Brak NVENC → automatyczny fallback na libx264 (wolniej).
- Cache renderu: `projects/<n>/cache/{preview,final}` — można usunąć, żeby zwolnić miejsce.
