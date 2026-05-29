# 🚗 Mercedes W204/W205 Otomoto Car Analyzer

A Python tool that scrapes otomoto.pl listings, scores every car, and opens an interactive HTML report in your browser — built around Warsaw SCT compliance, known engine issues, personal budget, and young-driver insurance costs.

---

## Features

- **Bulk search mode** — paste one otomoto search URL, auto-fetches every car across all result pages
- **Personal fit score** — combines condition, SCT compliance, budget, running cost, auto preference
- **Honest money math** — age-adjusted insurance (young drivers pay 2-3× more), 3-year TCO, monthly fuel/insurance/depreciation/maintenance
- **Affordability verdict** — per-car "Within budget / Stretch / Too expensive"
- **Negotiation target** — suggested offer price with discount % based on issues + mileage
- **Warsaw SCT 2026** — hard flags any non-compliant car (diesel < 2011 or petrol < 2005)
- **Known engine issues** — OM651 balance shaft, M271 timing chain, M272 balance shaft, M276 chain stretch, etc.
- **Otomoto signals** — surfaces platform's own BELOW/AT/ABOVE market rating, CEPiK verification, badges
- **Live HTML filters** — price slider, SCT-only, automatic-only, fuel type toggle
- **CSV export** — one click downloads full data as spreadsheet
- **Test drive checklist** — per-engine checklist modal + seller questions

---

## Requirements

- Python 3.9+
- Internet connection (scrapes otomoto.pl live)

Dependencies install automatically on first run:
```
requests
beautifulsoup4
```

---

## Installation

```bash
git clone https://github.com/Quantumns/otomoto-car-analyzer.git
cd otomoto-car-analyzer
python car_analyzer.py
```

No virtual environment needed — script auto-installs dependencies via pip if missing.

---

## Usage

### Step 1 — Open Command Prompt

Press `Windows + R` → type `cmd` → Enter

### Step 2 — Run the script

```bash
python car_analyzer.py
```

### Step 3 — Choose mode

```
Mode (1/2, default 1):
```

| Mode | What it does |
|------|-------------|
| **1** (recommended) | Paste one otomoto search URL → auto-fetches every matching car |
| **2** | Paste individual listing URLs one by one |

---

### Mode 1 — Search URL (recommended)

1. Go to **otomoto.pl**
2. Set your filters (example: Mercedes C-Class, from 2011, automatic, max price 35,000 PLN)
3. Copy the URL from your browser address bar
4. Paste it when prompted

```
Paste otomoto search URL: https://www.otomoto.pl/osobowe/mercedes-benz/c-klasa/...
How many result pages to scan? (default 5): 3
```

Scans ~32 cars per page. 3 pages = ~96 cars analyzed automatically.

### Mode 2 — Individual listings

```
URL: https://www.otomoto.pl/osobowe/oferta/mercedes-benz-...
URL: https://www.otomoto.pl/osobowe/oferta/mercedes-benz-...
URL: [blank line to finish]
```

---

### Step 4 — Read the report

Browser opens automatically with an interactive table sorted by **Fit Score**.

**Columns:**
| Column | Meaning |
|--------|---------|
| **Fit** | 0–100 personal fit (condition + budget + SCT + auto preference + otomoto price read) |
| **Listing** | Title, photo, badges (AUTO/MANUAL, CEPiK, BELOW/ABOVE market, low mileage…) |
| **Price** | Asking price + affordability verdict + negotiation target + 3-yr TCO |
| **vs Market** | % above/below estimated market average for that generation/year/mileage |
| **Year / KM** | Year, odometer, sanity check (suspiciously low/high average annual km) |
| **Engine** | Fuel, displacement, power, transmission, color |
| **Warsaw SCT** | ✅ compliant or 🚫 banned from Warsaw clean zone |
| **Known Issues** | Engine-specific flags (CRITICAL / HIGH / MEDIUM / LOW) with conditions |
| **Monthly Cost** | Fuel + insurance + depreciation + maintenance breakdown |

**Filters (top bar):**
- Drag price slider to hide cars over budget
- Check "SCT compliant only" to remove non-Warsaw cars
- Check "Automatic only"
- Select fuel type

**Export:** Click **⬇ Export CSV** to download all data as a spreadsheet.

**Test drive checklist:** Click the blue "📋 Test Drive Checklist" button on any car for engine-specific checks + seller questions.

---

## Personal Profile

Edit the `PROFILE` dict at the top of `car_analyzer.py` to match your situation:

```python
PROFILE = {
    "age": 22,                     # young driver → higher insurance
    "years_licensed": 4,
    "cash_budget": 25000,          # comfortable max PLN
    "stretch_budget": 33000,       # absolute max PLN
    "monthly_income": 5500,        # PLN net
    "monthly_cost_ceiling": 1200,  # max comfortable all-in monthly
    "city": "Warszawa",
    "prefer_automatic": True,
    "must_be_sct": True,           # hard requirement for Warsaw daily
}
```

All scores, verdicts, and insurance estimates adjust automatically.

---

## Score Explanation

**Fit Score** (headline, 0–100):
- Starts from condition score
- −40 if SCT non-compliant (hard requirement)
- +6/−8 for automatic/manual preference
- +8/−18 for budget fit
- +6/−4 for otomoto price evaluation (BELOW/ABOVE)
- +3 for CEPiK verification

**Condition Score** (0–100):
- −2 per year of age
- −1.8 per 10,000 km above 50k
- ±15/8/3 price vs market
- −25 if SCT non-compliant
- −20/−10/−4/−1 per CRITICAL/HIGH/MEDIUM/LOW issue
- −8 if odometer suspiciously low

---

## Warsaw SCT 2026 Compliance

| Fuel | Minimum year | Euro standard |
|------|-------------|---------------|
| Diesel | 2011 | Euro 5 |
| Petrol | 2005 | Euro 4 |

Cars below these thresholds cannot enter the Warsaw Clean Transport Zone. The tool flags these in red and deducts heavily from the score.

---

## Supported Engine Codes

| Code | Engine | Car |
|------|--------|-----|
| OM651 | 2.1 CDI diesel | W204/W205 — watch balance shaft pre-2012 |
| OM654 | 2.0 CDI diesel | W205 facelift — reliable |
| M271 | 1.8 petrol | W204 — watch timing chain pre-2010 |
| M274 | 2.0 petrol turbo | W205 — generally fine |
| M272 | 3.0 V6 petrol | W204/W205 — watch balance shaft pre-2009 |
| M276 | 3.5 V6 / AMG 43 biturbo | W205 — watch timing chain |
| M282 | 1.5 petrol | W205 — reliable |
| M270 | 1.6 petrol | W205 |

---

## Tips

- **Best value target:** W204 C200 CDI 2011–2013, automatic (7G-Tronic), 28–35k PLN
- **Avoid:** Pre-2011 diesel (SCT banned), pre-2010 M271 (timing chain risk)
- **Always:** Get independent OBD scan (~150–200 PLN) before buying
- **Negotiate:** Tool suggests an offer price — use the checklist issues as leverage
- **Insurance at 22:** Expect 8,000–15,000 PLN/year for performance models — tool reflects this

---

## Disclaimer

Scores and cost estimates are guidance only — not a substitute for a physical inspection, independent OBD diagnostic scan, or mechanic's assessment. Always verify mileage on [CEPiK](https://historiapojazdu.gov.pl) before buying.
