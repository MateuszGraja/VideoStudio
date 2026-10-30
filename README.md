# 🎬 Video Studio

**Montaż długiego filmu z kilkudziesięciu GB surowego materiału — bez umiejętności montażowych.**
Decyzje montażowe podejmuje [Claude Code](https://claude.com/claude-code), renderuje ffmpeg (na karcie NVIDIA), a Ty
oglądasz kolejne wersje w panelu w przeglądarce i piszesz uwagi zwykłym językiem.

```
 materiał (foldery z datami)          Claude Code + skill „video-studio”              panel http://127.0.0.1:8765
 ───────────────────────────    ──▶   ogląda arkusze klatek, opisuje klipy,    ──▶    podgląd wersji, oś czasu,
 DEVELOPMENT/2026_06_14 - …           układa rozdziały, dobiera muzykę,              postęp na żywo, uwagi
 COMPETITIONS/…/2026_09_15 - …        tnie na takty, renderuje (vs …)                „ten fragment za długi” ↩
```

## Co potrafi

- **Import bez kopiowania** – projekt trzyma ścieżki do oryginałów; data z metadanych/EXIF/nazwy pliku, a **data z nazwy
  folderu ma pierwszeństwo** (aparaty z zegarem z 2005 r. albo w UTC nie psują kolejności). Przeniesione pliki są
  podpinane automatycznie.
- **Analiza jakości** – ostrość, ekspozycja, drgania, cięcia, „najlepsze 4 sekundy” każdego klipu.
- **Arkusze klatek** – siatki kadrów z timestampami, przez które Claude „ogląda” materiał.
- **Montaż jako plik `edl.json`** – rozdziały, plansze tytułowe, napisy (data · miejsce), przejścia, stabilizacja,
  timelapse, pionowe klipy z rozmytym tłem, zdjęcia w ramce (bez zoomu), plansze „osoba” (portret + imię + rola).
- **Cięcie pod muzykę** – [Beat This!](https://github.com/CPJKU/beat_this) (ISMIR 2024) wykrywa beaty i początki taktów;
  każde ujęcie trwa pełne takty, cięcia lądują na „raz”. Sprawdzone na gotowym pliku: 99 % cięć w ±1 klatce od taktu.
- **Render** – podgląd 540p w minutę–dwie (cache: przelicza tylko zmienione ujęcia), finał 1080p z dwuprzebiegową
  normalizacją głośności (-14 LUFS) i rozdziałami w MP4 + plik rozdziałów dla YouTube.
- **Panel** – odtwarzacz wersji, oś czasu z miniaturami i ścieżką muzyki, biblioteka materiału pogrupowana po
  folderach, zakładka muzyki (upload, BPM, profil energii), feed „co teraz robi Claude”, uwagi do konkretnych ujęć,
  baner **„Ostatni stan”** po uruchomieniu.

---

## 1. Co trzeba pobrać

| Co | Po co | Jak |
|---|---|---|
| **Linux** (testowane: Ubuntu 24.04, GNOME) | system | – |
| **Python ≥ 3.10** (testowane 3.12) + `venv` | kod narzędzia | `sudo apt install python3 python3-venv` |
| **ffmpeg + ffprobe** (≥ 6) | cały render | `sudo apt install ffmpeg` |
| **curl**, **git** | instalator, panel | `sudo apt install curl git` |
| **Sterownik NVIDIA** *(opcjonalnie)* | szybkie kodowanie NVENC + Beat This! na GPU | `sudo ubuntu-drivers install` |
| **Claude Code** | „montażysta” | `curl -fsSL https://claude.ai/install.sh \| bash` → potem `claude` i zalogowanie |
| ~**8 GB** miejsca na `.venv` | PyTorch z bibliotekami CUDA (Beat This!) | instaluje się sam; bez niego: `--no-beat` (~300 MB) |

Bez karty NVIDIA wszystko też działa: render przełącza się na `libx264` (wolniej), Beat This! liczy na CPU.

Pakiety Pythona (instalują się same z `requirements.txt`): Flask, Pillow, NumPy, OpenCV, librosa, soundfile, Beat This!
(+ PyTorch). Czcionki Montserrat i Inter (licencja OFL) są w repo.

## 2. Instalacja

```bash
git clone https://github.com/MateuszGraja/VideoStudio.git ~/video-studio
cd ~/video-studio
./install.sh --desktop        # --desktop = skróty na pulpicie; --no-beat = bez PyTorcha
```

Instalator: tworzy `.venv` i instaluje pakiety, linkuje komendę `vs` do `~/.local/bin/`, linkuje skill dla Claude Code
do `~/.claude/skills/video-studio`, opcjonalnie tworzy skróty **Video Studio** i **Video Studio + Claude**.
Można go uruchamiać wielokrotnie.

Sprawdzenie:
```bash
vs --help
./start.sh                    # panel otworzy się w przeglądarce
```

> Przy pierwszej analizie muzyki Beat This! pobiera wagi modelu (~80 MB) — potrzebny internet.

## 3. Pierwszy projekt

**Ułóż materiał w folderach** – struktura folderów = struktura opowieści, a nazwa folderu staje się napisem w filmie:

```
~/Wideo/moj-projekt/
├── DEVELOPMENT/
│   ├── 2026_06_14 - pierwszy oblot konstrukcji/
│   ├── 2026_08_10 - loty tragedia/
│   └── MODELARNIA/                       (foldery bez daty też OK)
├── COMPETITIONS/
│   └── 09_10 - 09_22 SUAS/
│       ├── 2026_09_15 - zawody dzien 1 + rozbicie/
│       └── 2026_09_18 - zwiedzanie waszyngton/
└── TEAM/
    └── Imie_Nazwisko_Rola.jpg            (nazwa pliku → podpis na planszy zespołu)
```

**Załóż projekt i daj pracę Claude'owi:**
```bash
vs new moj-film --source ~/Wideo/moj-projekt
cd ~/Wideo/moj-projekt && claude
```
i napisz np.: *„Zmontuj z tego 10–15 minutowy film o naszym projekcie, bez narracji, tylko muzyka.”*
Skill `video-studio` prowadzi Claude'a przez cały proces: import → analiza → opisanie każdego klipu → rozdziały →
`edl.json` → render podglądu.

**Muzyka:** wrzuć pliki audio w panelu (zakładka *Muzyka*) albo do `projects/<projekt>/music/`. Przy utworze możesz
wpisać „nastrój / gdzie użyć”.

**Uwagi:** w panelu pod odtwarzaczem lub przy konkretnym ujęciu („za długie”, „więcej lotów”, „zamień utwór”) —
Claude odczytuje je przed kolejną wersją (`vs feedback`).

**Finał:** poproś o wersję końcową albo `vs render --final` → `projects/<projekt>/renders/*.mp4` (+ `.chapters.txt`).

## 4. Uruchamianie po restarcie komputera

- skrót **Video Studio** – sam panel (oglądanie wersji, uwagi),
- skrót **Video Studio + Claude** – panel + terminal z `claude --continue` w folderze materiału, czyli **ta sama
  rozmowa z pełnym kontekstem**,
- albo ręcznie: `~/video-studio/start.sh [--claude]`.

Cały stan (montaż, opisy, rendery, uwagi, historia) jest w `projects/<projekt>/` – nic nie trzeba zapisywać.

## 5. Co zmienić u siebie

| Chcesz… | Zmień |
|---|---|
| inną lokalizację repo | dowolna — skrypty same znajdują swój katalog; po przeniesieniu uruchom ponownie `./install.sh` (odświeży linki i skróty) |
| inny port panelu | `VS_PORT=9000 ./start.sh` lub `vs serve --port 9000` |
| inny terminal niż GNOME (KDE, XFCE…) | `VS_TERMINAL=konsole ./start.sh --claude` (terminal musi rozumieć `--working-directory` i `--`), albo popraw ostatnie linie `start.sh` |
| rozmowę Claude w innym folderze | `VS_CLAUDE_DIR=/ścieżka ./start.sh --claude` (domyślnie: pierwszy folder materiału aktywnego projektu) |
| dodać materiał | wrzuć pliki do folderów i napisz Claude'owi „dodałem materiał” (`vs ingest` dopisze tylko nowe) |
| wspólną muzykę dla wielu projektów | wrzuć do `~/video-studio/music/` |
| rozdzielczość / fps / look / głośność | `settings` w `edl.json`: `width`, `height`, `fps`, `look` (`none warm cool punchy film bw`), `loudness`, `accent` |
| styl zdjęć | element `image`: `"style": "card"` (ramka, domyślnie u nas), `"person"` + `name`/`role`, albo stary `kenburns: in/out` |
| szybszy/wolniejszy render | `vs render --workers 4` (równoległe ujęcia) |
| sposób pracy Claude'a | `claude-skill/video-studio/SKILL.md` (zasady montażu, długości ujęć, styl napisów) |

## 6. Komendy `vs`

```
vs new <nazwa> --source <folder>   nowy projekt            vs timeline         sprawdź edl.json (czasy, błędy)
vs use <nazwa> / vs projects       aktywny projekt         vs render [--final] [--items 10-25]
vs ingest [--add <folder>]         import / nowe pliki     vs still <idx> [--at s]  klatka z elementu
vs analyze [ids] [--force]         analiza jakości         vs beats <plik> [--bars] [--force]  rytm utworu
vs list [--best --group G --date] lista klipów            vs music            muzyka projektu
vs info <id> / vs sheet <id>       szczegóły / arkusz      vs note "…"         komunikat do panelu
vs tag / vs tagjson <plik.json>    opisy klipów            vs feedback [--resolve N|all]
vs chapters [--set '[…]']          rozdziały               vs status / vs serve [--port]
```

## 7. Jak to działa w środku

```
studio/ingest.py    skan folderów → media.json (daty, rotacja, HDR, miniatury, arkusze klatek)
studio/analyze.py   4 kl/s w 320×180: ostrość (Laplacian), jasność, ruch, drgania (phaseCorrelate), cięcia
studio/music.py     Beat This! → beaty + downbeaty; librosa → energia, akcenty (fallback: beat_track)
studio/timeline.py  edl.json → czasy elementów (wyrównane do klatek, z nakładkami przejść)
studio/render.py    ujęcie → segment (cache po hashu) → bloki xfade (ucinane do dokładnej liczby klatek)
                    → concat bez rekompresji → miks muzyki + loudnorm → MP4 z rozdziałami
studio/graphics.py  plansze, napisy, ramki zdjęć, plansze osób (Pillow → PNG z alfą)
studio/server.py    Flask: API panelu, strumieniowanie wideo, upload muzyki, uwagi
panel/index.html    panel (vanilla JS, odpytuje /state co 1,5 s)
```

Dane projektu (`projects/<nazwa>/`, poza gitem): `media.json`, `edl.json`, `project.json`, `thumbs/`, `sheets/`,
`analysis/`, `music/`, `music_analysis/`, `cache/`, `renders/`, `events.jsonl`, `feedback.json`, `status.json`.
Długi montaż wygodnie generować skryptem `projects/<nazwa>/tools/build_edl.py` (lista ujęć → `edl.json`, napisy dni
z nazw folderów, dopasowanie do taktów) — Claude tworzy go sam przy dużych projektach.

## 8. Rozwiązywanie problemów

| Objaw | Rozwiązanie |
|---|---|
| `vs: command not found` | dodaj `~/.local/bin` do `PATH` i otwórz nowy terminal |
| panel się nie otwiera | `./start.sh`, log: `panel.log`; czy port 8765 nie jest zajęty (`VS_PORT`) |
| render bardzo wolny | brak NVENC → `ffmpeg -encoders \| grep nvenc`; zainstaluj sterownik NVIDIA / ffmpeg z NVENC |
| błąd renderu | `projects/<n>/cache/ffmpeg.log`, `vs status` |
| `Beat This! niedostępny` w panelu | brak PyTorcha (`./install.sh` bez `--no-beat`) — działa dalej na librosa |
| mało miejsca | usuń `projects/<n>/cache/` (przeliczy się przy następnym renderze) |
| plik przeniesiony | `vs ingest` – podepnie po nazwie i rozmiarze, opisy zostają |

## Prywatność i licencje

- Repo zawiera **tylko kod**. Materiał, opisy klipów, montaże, rendery i muzyka (`projects/`, `music/`) są w
  `.gitignore` – zostają na Twoim dysku. Projekt nie używa żadnych kluczy API (Claude Code loguje się sam).
- Czcionki Montserrat i Inter: SIL Open Font License 1.1 (`fonts/OFL-*.txt`).
- Uwaga na muzykę: znane, komercyjne utwory są OK w prywatnym filmie, ale na YouTube mogą zablokować / wyciszyć film.
  Darmowe źródła: Pixabay Music, YouTube Audio Library, Free Music Archive.
