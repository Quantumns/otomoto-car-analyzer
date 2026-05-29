"""
Mercedes W204/W205 Otomoto Car Analyzer — v3
- Paste a SEARCH URL → auto-fetch every listing across all result pages
- Or paste individual listing URLs
- Personal profile: age-based insurance, affordability verdict, negotiation target, 3-yr TCO
- Surfaces otomoto's own price evaluation (BELOW/IN/ABOVE) + CEPiK verification
- Personal-fit score, live HTML filters, CSV export
"""

import json
import sys
import re
import csv
import io
import time
import webbrowser
import tempfile
import os
from datetime import datetime

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Installing required packages...")
    os.system(f"{sys.executable} -m pip install requests beautifulsoup4")
    import requests
    from bs4 import BeautifulSoup

CURRENT_YEAR = 2026
EUR_TO_PLN = 4.27

# ── PERSONAL PROFILE ──────────────────────────────────────────────────────
# Edit these to match the buyer. Drives insurance, affordability, fit score.
PROFILE = {
    "age": 22,                  # young driver → much higher insurance
    "years_licensed": 4,        # affects insurance bonus-malus
    "cash_budget": 25000,       # realistic out-of-pocket PLN
    "stretch_budget": 33000,    # absolute max if the car is special
    "monthly_income": 5500,     # PLN net
    "monthly_cost_ceiling": 1200,  # max comfortable all-in monthly running cost
    "city": "Warszawa",         # daily commute → SCT mandatory
    "prefer_automatic": True,   # 7G-Tronic / auto strongly preferred
    "prefer_diesel_ok": True,   # diesel fine if SCT compliant
    "must_be_sct": True,        # hard requirement — daily Warsaw driving
    "want_sunroof": True,       # wife wants a sunroof — bonus if present
    "avoid_damaged": True,      # skip/penalize accident-damaged cars
    "avoid_rhd": True,          # right-hand-drive (UK import) is a hassle in PL
    "wary_of_imports": False,   # imports OK but flagged for extra history check
}

# ── SCT (Warsaw Clean Transport Zone, Jan 2026) ───────────────────────────
SCT_MIN_DIESEL_YEAR = 2011   # Euro 5+
SCT_MIN_PETROL_YEAR = 2005   # Euro 4+

# ── Monthly running cost estimates (PLN) ──────────────────────────────────
FUEL_PRICE_PER_LITER = {"petrol": 6.50, "diesel": 6.10, "hybrid": 6.50}  # PLN/L, 2026 Poland
AVG_KM_PER_MONTH = 1500

INSURANCE_BRACKET = {
    # (engine_code or "default") → rough annual OC+AC PLN
    "M271": 3200, "M272": 4500, "M276": 5200,
    "OM651": 3400, "M274": 3600, "OM654": 3500,
    "M282": 3300, "M276AMG43": 7500,
    "default": 4000,
}

# ── Known issues per engine ───────────────────────────────────────────────
KNOWN_ISSUES = {
    "OM651": [
        ("CRITICAL", "Balance shaft failure — common pre-2012, very expensive repair (~8,000 PLN)", lambda y, km: y < 2012),
        ("HIGH",     "High-pressure fuel pump wear above 150k km", lambda y, km: km > 150000),
        ("MEDIUM",   "Injector seal leaks (oil in injector cups)", lambda y, km: km > 120000),
        ("MEDIUM",   "DPF clogging if driven mostly in city", lambda y, km: True),
        ("LOW",      "EGR valve fouling — clean every 60k km", lambda y, km: True),
    ],
    "M271": [
        ("CRITICAL", "Timing chain tensioner failure — can destroy engine if ignored (pre-2010)", lambda y, km: y < 2010),
        ("HIGH",     "Balance shaft wear, especially pre-2011 with high mileage", lambda y, km: y < 2011 and km > 100000),
        ("MEDIUM",   "Oil consumption increases above 150k km", lambda y, km: km > 150000),
        ("LOW",      "Throttle body fouling — clean every 80k km", lambda y, km: True),
    ],
    "M272": [
        ("HIGH",     "Balance shaft / idler gear wear — pre-2009 engines especially", lambda y, km: y < 2009),
        ("MEDIUM",   "Intake manifold actuator failure (common, ~600 PLN fix)", lambda y, km: True),
        ("LOW",      "Spark plugs and coils every 40k km", lambda y, km: True),
    ],
    "M276": [
        ("MEDIUM",   "Timing chain stretch — check oil level regularly, replace at first rattle", lambda y, km: km > 120000),
        ("LOW",      "Cam adjuster solenoid fouling (easy fix, ~400 PLN)", lambda y, km: km > 100000),
    ],
    "M274": [
        ("LOW",      "Oil consumption if service intervals were skipped", lambda y, km: km > 100000),
        ("LOW",      "Timing chain — generally fine but monitor with service history", lambda y, km: km > 150000),
    ],
    "M282": [
        ("LOW",      "Generally very reliable; ensure oil was changed on schedule", lambda y, km: True),
    ],
    "OM654": [
        ("LOW",      "Very reliable engine; watch DPF if used for short city trips", lambda y, km: True),
        ("LOW",      "AdBlue system — check level, refill every ~15k km", lambda y, km: True),
    ],
}

W204_COMMON = [
    ("LOW", "Check for rust on door sills and wheel arches (older cars)", lambda y, km: y < 2012),
    ("LOW", "Automatic gearbox fluid — should be changed every 80k km", lambda y, km: km > 100000),
]

W205_COMMON = [
    ("MEDIUM", "Electric power steering rack — check for play/leak on test drive", lambda y, km: km > 80000),
    ("LOW",    "COMAND infotainment glitches on pre-2017 models (software update helps)", lambda y, km: y < 2017),
    ("LOW",    "Panoramic sunroof drain channels — clean annually to prevent leaks", lambda y, km: True),
]

# ── Market average price references (PLN, 2026 estimate) ─────────────────
# (generation, fuel_key, year_from, year_to, km_max, avg_pln)
MARKET_REF = [
    # W204
    ("W204", "diesel", 2011, 2014, 100000, 38000),
    ("W204", "diesel", 2011, 2014, 999999, 29000),
    ("W204", "petrol", 2011, 2014, 100000, 34000),
    ("W204", "petrol", 2011, 2014, 999999, 27000),
    ("W204", "petrol", 2007, 2010, 150000, 22000),
    ("W204", "diesel", 2007, 2010, 150000, 21000),
    # W205 diesel
    ("W205", "diesel", 2014, 2016, 120000, 58000),
    ("W205", "diesel", 2014, 2016, 999999, 47000),
    ("W205", "diesel", 2016, 2018, 120000, 70000),
    ("W205", "diesel", 2016, 2018, 999999, 57000),
    ("W205", "diesel", 2018, 2021, 100000, 95000),
    ("W205", "diesel", 2018, 2021, 999999, 80000),
    # W205 petrol
    ("W205", "petrol", 2014, 2016, 120000, 52000),
    ("W205", "petrol", 2014, 2016, 999999, 43000),
    ("W205", "petrol", 2016, 2018, 120000, 65000),
    ("W205", "petrol", 2016, 2018, 999999, 53000),
    ("W205", "petrol", 2018, 2021, 100000, 90000),
    ("W205", "petrol", 2018, 2021, 999999, 75000),
    # W205 AMG 43 (special — petrol, high power)
    ("W205", "amg43",  2016, 2019, 120000, 100000),
    ("W205", "amg43",  2016, 2019, 999999,  85000),
    ("W205", "amg43",  2019, 2021, 120000, 130000),
]

# ── Test drive checklist per engine/model ─────────────────────────────────
TEST_DRIVE_CHECKLIST = {
    "OM651": [
        "Start cold — listen for diesel knock or rattle (balance shaft)",
        "Check for white/blue smoke on startup and under load",
        "Request full service history — look for balance shaft replacement record",
        "Run OBD scan: check for fuel pressure, injector, and DPF fault codes",
        "Inspect injector cups for oil contamination (remove engine cover)",
        "Test DPF: ask if the car is used mainly for short trips",
    ],
    "M271": [
        "Cold start — listen for timing chain rattle (fatal if ignored)",
        "Check engine oil level — low oil = chain wear risk",
        "Inspect for oil leaks on valve cover and cam covers",
        "Run OBD scan: camshaft position sensor, chain stretch codes",
        "Check service history: was timing chain replaced?",
    ],
    "M272": [
        "Listen for engine roughness or vibration at idle (balance shaft)",
        "Check for intake manifold actuator failure (rough idle, check engine light)",
        "OBD scan for camshaft position codes",
        "Check spark plugs and coil pack condition",
    ],
    "M276": [
        "Warm up engine fully — listen for timing chain rattle at idle",
        "Check oil level and look for sludge on dipstick",
        "OBD scan for cam adjuster codes (P0010, P0013 series)",
    ],
    "M274": [
        "Check oil consumption: ask seller how often they top up between services",
        "Look for signs of coolant or oil leaks",
        "OBD scan for turbo pressure codes",
    ],
    "M282": [
        "Generally trouble-free — focus on service history completeness",
        "Check DSG/9G-TRONIC gearbox for smooth shifting through all gears",
    ],
    "OM654": [
        "Check AdBlue level and system status (no warning lights)",
        "DPF condition — run OBD scan for regeneration frequency",
        "Cold start — no excessive smoke is a good sign",
    ],
    "Unknown": [
        "Identify exact engine before buying — ask seller for service book",
        "Run OBD diagnostic scan regardless of engine type",
    ],
}

W205_TEST_DRIVE = [
    "Steering: drive slowly and turn full lock both ways — feel for play or grinding (steering rack)",
    "Check all electric functions: windows, mirrors, sunroof, seat memory",
    "COMAND screen: verify no frozen image or reboot loops",
    "Check for water stains under rear parcel shelf (sunroof drain issue)",
    "Inspect door sills and wheel arches for stone chip rust",
    "Test all drive modes if equipped (Sport/Comfort/Eco)",
]

W204_TEST_DRIVE = [
    "Check for rust on door sills, trunk lid edges, wheel arches",
    "Test automatic gearbox: should shift smoothly without hesitation or slipping",
    "Inspect for oil leaks around engine — W204 gaskets can weep",
    "Check COMAND and all electrics — aging infotainment units can fail",
    "Push brake pedal firmly — no vibration or pulling to one side",
]

QUESTIONS_FOR_SELLER = [
    "Do you have the full service book with stamps? Can I see it now?",
    "Has the car ever been in an accident? Any insurance (szkoda) claims?",
    "Why are you selling?",
    "How long have you owned it? Where did you buy it from?",
    "Is the mileage correct and unmodified?",
    "Where was it serviced — authorized dealer or independent?",
    "Any warning lights currently on or recently reset?",
    "Are you OK with me taking it to an independent mechanic or diagnostic center?",
    "Is the price negotiable?",
]


def get_market_avg(generation, fuel, year, mileage, engine_code=""):
    f = fuel.lower()
    is_diesel = "diesel" in f or "cdi" in f
    # Special AMG 43 bucket
    if engine_code in ("M276",) and "amg" in (fuel + generation).lower():
        fuel_key = "amg43"
    elif is_diesel:
        fuel_key = "diesel"
    else:
        fuel_key = "petrol"

    best = None
    for gen, fk, y1, y2, km_max, price in MARKET_REF:
        if gen == generation and fk == fuel_key and y1 <= year <= y2 and mileage <= km_max:
            best = price
    return best


def detect_generation(title_hint, year):
    t = title_hint.lower()
    if "w205" in t or (2014 <= year <= 2021):
        return "W205"
    if "w204" in t or (2007 <= year <= 2014):
        return "W204"
    return "W204" if year < 2015 else "W205"


def detect_engine_code(engine_str, fuel, power_str, title=""):
    s = (engine_str or "").replace(" ", "").replace("\xa0", "").lower()
    f = (fuel or "").lower()
    t = (title or "").lower()
    is_diesel = "diesel" in f or "cdi" in f

    # Try to extract numeric displacement
    nums = re.findall(r"\d+", s)
    disp = int(nums[0]) if nums else 0

    if is_diesel:
        if 2050 <= disp <= 2200 or "2.1" in s or "2100" in s:
            return "OM651"
        if 1900 <= disp <= 2000 or "2.0" in s:
            return "OM654"
    else:
        if 1450 <= disp <= 1550 or "1.5" in s:
            return "M282"
        if 1550 <= disp <= 1650 or "1.6" in s:
            return "M270"
        if 1750 <= disp <= 1850 or "1.8" in s:
            return "M271"
        if 1950 <= disp <= 2100 or "2.0" in s:
            return "M274"
        if 2900 <= disp <= 3100 or "3.0" in s:
            # AMG 43 uses biturbo 3.0 M276 derivative
            if "amg" in t and "43" in t:
                return "M276"  # treated as AMG43 variant
            return "M272"
        if 3400 <= disp <= 3600 or "3.5" in s:
            return "M276"
    return None


def check_sct(fuel, year):
    f = (fuel or "").lower()
    is_diesel = "diesel" in f or "cdi" in f
    if is_diesel:
        if year >= SCT_MIN_DIESEL_YEAR:
            euro = "Euro 6" if year >= 2015 else "Euro 5"
            return True, f"{euro} diesel — SCT compliant ✓"
        return False, f"Pre-2011 diesel — NOT compliant (Euro ≤ 4). Cannot enter Warsaw SCT zone."
    else:
        if year >= SCT_MIN_PETROL_YEAR:
            euro = "Euro 6" if year >= 2015 else ("Euro 5" if year >= 2011 else "Euro 4")
            return True, f"{euro} petrol — SCT compliant ✓"
        return False, "Pre-2005 petrol — NOT compliant."


def get_issues(engine_code, generation, year, mileage):
    issues = []
    if engine_code and engine_code in KNOWN_ISSUES:
        for sev, desc, cond in KNOWN_ISSUES[engine_code]:
            if cond(year, mileage):
                issues.append((sev, desc))
    gen_issues = W205_COMMON if generation == "W205" else W204_COMMON
    for sev, desc, cond in gen_issues:
        if cond(year, mileage):
            issues.append((sev, desc))
    return issues


def odometer_sanity(year, mileage):
    age = CURRENT_YEAR - year
    if age <= 0:
        return None, None
    avg = mileage / age
    if avg < 5000:
        return "LOW", f"Avg {avg:,.0f} km/year — suspiciously low. Verify on CEPiK."
    if avg > 35000:
        return "HIGH", f"Avg {avg:,.0f} km/year — very high usage. Heavy wear likely."
    if avg > 25000:
        return "ELEVATED", f"Avg {avg:,.0f} km/year — above average. Likely fleet/taxi use."
    return "NORMAL", f"Avg {avg:,.0f} km/year — normal usage."


def age_insurance_multiplier(age, years_licensed):
    """Young Polish drivers pay dramatically more for OC/AC."""
    if age < 24:
        m = 2.6        # 22yo: ~2.5-3x base
    elif age < 26:
        m = 2.0
    elif age < 30:
        m = 1.5
    else:
        m = 1.0
    # A few years of clean history claws some of it back
    if years_licensed >= 5:
        m *= 0.85
    elif years_licensed >= 3:
        m *= 0.92
    return m


def affordability(price_pln, monthly_total):
    """Verdict vs PROFILE budget + monthly ceiling."""
    cash = PROFILE["cash_budget"]
    stretch = PROFILE["stretch_budget"]
    ceil = PROFILE["monthly_cost_ceiling"]

    if price_pln <= cash:
        price_verdict, pcolor = "Within budget", "#28a745"
    elif price_pln <= stretch:
        price_verdict, pcolor = "Stretch — over comfort budget", "#fd7e14"
    else:
        over = price_pln - stretch
        price_verdict, pcolor = f"Too expensive (+{over:,} PLN over max)", "#dc3545"

    if monthly_total <= ceil:
        run_verdict, rcolor = "Running cost OK", "#28a745"
    elif monthly_total <= ceil * 1.25:
        run_verdict, rcolor = "Running cost tight", "#fd7e14"
    else:
        run_verdict, rcolor = "Running cost too high", "#dc3545"

    # income share of monthly running cost
    income_share = round(monthly_total / PROFILE["monthly_income"] * 100)
    return {
        "price_verdict": price_verdict, "price_color": pcolor,
        "run_verdict": run_verdict, "run_color": rcolor,
        "income_share": income_share,
    }


def negotiation_target(price_pln, market_avg, price_indicator, issues, mileage,
                       days_on_market=0, price_dropped=False, damaged=False):
    """Suggest a realistic offer price + the leverage points behind it."""
    if price_pln <= 0:
        return None
    base = market_avg if market_avg else price_pln
    target = min(price_pln, base)
    crit = sum(1 for s, _ in issues if s == "CRITICAL")
    high = sum(1 for s, _ in issues if s == "HIGH")
    leverage = []
    discount = 0.04
    if crit:
        discount += crit * 0.05
        leverage.append(f"{crit} critical engine risk(s)")
    if high:
        discount += high * 0.025
        leverage.append(f"{high} high-severity issue(s)")
    if mileage > 200000:
        discount += 0.03
        leverage.append("very high mileage")
    if price_indicator == "ABOVE":
        discount += 0.03
        leverage.append("Otomoto rates it above market")
    if days_on_market >= 60:
        discount += 0.04
        leverage.append(f"listed {days_on_market} days — stale, seller likely motivated")
    elif days_on_market >= 30:
        discount += 0.02
        leverage.append(f"listed {days_on_market} days")
    if price_dropped:
        discount += 0.02
        leverage.append("price already dropped — momentum on your side")
    if damaged:
        discount += 0.05
        leverage.append("accident history disclosed")
    discount = min(discount, 0.25)
    target = round(target * (1 - discount) / 100) * 100
    savings = price_pln - target
    return {"target": target, "savings": max(0, savings),
            "discount_pct": round(discount * 100), "leverage": leverage}


def three_year_tco(price_pln, costs, market_avg):
    """Total cost of ownership over 3 years (purchase - resale + running)."""
    # crude resale: depreciate ~7%/yr compounding from current value
    resale = round(price_pln * (0.93 ** 3))
    running_3y = (costs["fuel"] + costs["insurance"] + costs["maintenance"]) * 36
    depreciation = price_pln - resale
    total = depreciation + running_3y
    return {
        "resale": resale,
        "depreciation": depreciation,
        "running_3y": running_3y,
        "total": total,
        "per_month": round(total / 36),
    }


def personal_fit_score(car):
    """0-100: how well this specific car fits the PROFILE, beyond raw condition."""
    score = car["score"]  # start from condition score
    # SCT hard requirement
    if PROFILE["must_be_sct"] and not car["sct_ok"]:
        score -= 40
    # automatic preference
    trans = (car.get("transmission") or "").lower()
    is_auto = "auto" in trans or "tronic" in trans or "dsg" in trans
    if PROFILE["prefer_automatic"]:
        score += 6 if is_auto else -8
    # budget fit
    aff = car.get("afford", {})
    if aff.get("price_color") == "#28a745":
        score += 8
    elif aff.get("price_color") == "#dc3545":
        score -= 18
    # running cost fit
    if aff.get("run_color") == "#28a745":
        score += 4
    elif aff.get("run_color") == "#dc3545":
        score -= 10
    # otomoto's own price read
    pi = car.get("price_indicator")
    if pi == "BELOW":
        score += 6
    elif pi == "ABOVE":
        score -= 4
    # CEPiK verified = trust
    if car.get("cepik"):
        score += 3
    # accident damage — big red flag
    if PROFILE["avoid_damaged"] and car.get("damaged"):
        score -= 25
    # right-hand drive import — hassle in Poland
    if PROFILE["avoid_rhd"] and car.get("rhd"):
        score -= 12
    # imported — minor caution
    if car.get("imported") and PROFILE["wary_of_imports"]:
        score -= 5
    # sunroof — wife wants it
    if PROFILE["want_sunroof"] and car.get("sunroof"):
        score += 5
    # price already dropped = motivated seller
    if car.get("price_dropped"):
        score += 2
    return max(0, min(100, round(score)))


def estimate_monthly_cost(fuel, engine_cap_str, engine_code, price_pln):
    f = (fuel or "").lower()
    is_diesel = "diesel" in f or "cdi" in f
    fuel_key = "diesel" if is_diesel else "petrol"

    # Estimate L/100km from engine size
    try:
        disp_str = re.sub(r"[^\d]", "", (engine_cap_str or ""))
        disp = int(disp_str) if disp_str else 2000
    except Exception:
        disp = 2000

    is_amg = "amg" in (engine_code or "").lower() or disp >= 2996
    if disp < 1600:
        consumption = 6.5 if not is_diesel else 5.0
    elif disp < 2200:
        consumption = 8.5 if not is_diesel else 6.2
    elif disp < 3100:
        # AMG 43 (biturbo 3.0): significantly higher
        consumption = 14.0 if is_amg else 10.5
        if is_diesel:
            consumption = 7.5
    else:
        consumption = 13.0 if not is_diesel else 8.5

    cost_per_km = FUEL_PRICE_PER_LITER[fuel_key] * consumption / 100
    monthly_fuel = round(cost_per_km * AVG_KM_PER_MONTH)

    # Insurance estimate — scaled by driver age (young drivers pay far more)
    ins_annual = INSURANCE_BRACKET.get(engine_code, INSURANCE_BRACKET["default"])
    ins_mult = age_insurance_multiplier(PROFILE["age"], PROFILE["years_licensed"])
    ins_annual = round(ins_annual * ins_mult)
    monthly_ins = round(ins_annual / 12)

    # Rough depreciation (5–8% of price per year)
    dep_rate = 0.07
    monthly_dep = round(price_pln * dep_rate / 12)

    # Basic maintenance estimate (~2500 PLN/year for older, ~1500 for newer)
    monthly_maint = 250 if CURRENT_YEAR - 2014 > 5 else 150

    total = monthly_fuel + monthly_ins + monthly_dep + monthly_maint
    return {
        "fuel": monthly_fuel,
        "insurance": monthly_ins,
        "depreciation": monthly_dep,
        "maintenance": monthly_maint,
        "total": total,
        "consumption": consumption,
        "fuel_type": fuel_key,
        "ins_annual": ins_annual,
        "ins_mult": round(ins_mult, 2),
    }


def score_car(price, mileage, year, sct_ok, issues, market_avg, odometer_flag):
    score = 100

    # Age (2 pts per year)
    age = CURRENT_YEAR - year
    score -= age * 2

    # Mileage (scaled — steeper above 120k)
    if mileage > 50000:
        excess = mileage - 50000
        score -= (excess / 10000) * 1.8

    # Price vs market
    if market_avg and price > 0:
        ratio = price / market_avg
        if ratio < 0.80:
            score += 15
        elif ratio < 0.90:
            score += 8
        elif ratio < 0.97:
            score += 3
        elif ratio > 1.20:
            score -= 15
        elif ratio > 1.10:
            score -= 8
        elif ratio > 1.03:
            score -= 3

    # SCT
    if not sct_ok:
        score -= 25

    # Known issues
    severity_penalty = {"CRITICAL": 20, "HIGH": 10, "MEDIUM": 4, "LOW": 1}
    for sev, _ in issues:
        score -= severity_penalty.get(sev, 0)

    # Odometer suspicion
    if odometer_flag == "LOW":
        score -= 8
    elif odometer_flag == "HIGH":
        score -= 5
    elif odometer_flag == "ELEVATED":
        score -= 2

    return max(0, min(100, round(score)))


def build_car(raw):
    """raw = dict of extracted basics; returns fully enriched car dict.
    Required keys: url,title,price_pln,price_display,currency,year,mileage,fuel,
    engine_cap,power,transmission,color,version,body_type,has_vin,seller,
    seller_type,location,generation_hint,photos,price_indicator,cepik
    """
    year = raw["year"]
    mileage = raw["mileage"]
    fuel_val = raw["fuel"]
    price_pln = raw["price_pln"]

    generation = detect_generation(raw.get("generation_hint", "") + " " + raw["title"], year)
    engine_code = detect_engine_code(raw["engine_cap"], fuel_val, raw["power"], raw["title"])
    sct_ok, sct_note = check_sct(fuel_val, year)
    issues = get_issues(engine_code, generation, year, mileage)
    market_avg = get_market_avg(generation, fuel_val, year, mileage, engine_code or "")
    odo_flag, odo_note = odometer_sanity(year, mileage)
    score = score_car(price_pln, mileage, year, sct_ok, issues, market_avg, odo_flag)
    costs = estimate_monthly_cost(fuel_val, raw["engine_cap"], engine_code, price_pln)
    afford = affordability(price_pln, costs["total"])
    damaged = raw.get("damaged", False)
    days_on_market = raw.get("days_on_market", 0)
    price_dropped = raw.get("price_dropped", False)
    nego = negotiation_target(price_pln, market_avg, raw.get("price_indicator"), issues,
                              mileage, days_on_market, price_dropped, damaged)
    tco = three_year_tco(price_pln, costs, market_avg)

    td_list = TEST_DRIVE_CHECKLIST.get(engine_code or "Unknown", TEST_DRIVE_CHECKLIST["Unknown"])
    gen_td = W205_TEST_DRIVE if generation == "W205" else W204_TEST_DRIVE
    full_checklist = td_list + gen_td

    car = {
        "url": raw["url"],
        "title": raw["title"],
        "price": price_pln,
        "price_display": raw["price_display"],
        "currency": raw["currency"],
        "year": year,
        "mileage": mileage,
        "fuel": fuel_val,
        "engine_cap": raw["engine_cap"],
        "power": raw["power"],
        "transmission": raw["transmission"],
        "color": raw.get("color", ""),
        "version": raw.get("version", ""),
        "body_type": raw.get("body_type", ""),
        "has_vin": raw.get("has_vin", False),
        "seller": raw.get("seller", ""),
        "seller_type": raw.get("seller_type", ""),
        "location": raw.get("location", ""),
        "generation": generation,
        "engine_code": engine_code or "Unknown",
        "sct_ok": sct_ok,
        "sct_note": sct_note,
        "issues": issues,
        "market_avg": market_avg,
        "score": score,
        "photos": raw.get("photos", []),
        "odo_flag": odo_flag,
        "odo_note": odo_note,
        "costs": costs,
        "checklist": full_checklist,
        "price_indicator": raw.get("price_indicator"),
        "cepik": raw.get("cepik", False),
        "badges": raw.get("badges", []),
        "afford": afford,
        "nego": nego,
        "tco": tco,
        # deep-scan enrichment (present when detail page fetched)
        "damaged": damaged,
        "imported": raw.get("imported", False),
        "rhd": raw.get("rhd", False),
        "sunroof": raw.get("sunroof", False),
        "has_dpf": raw.get("has_dpf", False),
        "days_on_market": days_on_market,
        "price_dropped": price_dropped,
        "price_drop_amount": raw.get("price_drop_amount", 0),
        "equipment": raw.get("equipment", []),
        "description": raw.get("description", ""),
        "deep": raw.get("deep", False),
        "source": raw.get("source", "otomoto"),
    }
    car["fit"] = personal_fit_score(car)
    return car


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _get_next_data(url):
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not tag:
        return None
    return json.loads(tag.string)


def _node_to_car(node):
    """Convert a search-result edge node into the raw dict for build_car."""
    params = {}
    for p in node.get("parameters", []) or []:
        k = p.get("key")
        if k:
            params[k] = {"value": p.get("value", ""), "display": p.get("displayValue", "")}

    def pv(k):
        return params.get(k, {}).get("value", "")

    def pd(k):
        return params.get(k, {}).get("display", "")

    try:
        year = int(pv("year") or 0)
    except (ValueError, TypeError):
        year = 0
    try:
        mileage = int(re.sub(r"[^\d]", "", pv("mileage") or "0") or 0)
    except (ValueError, TypeError):
        mileage = 0

    price_units = 0
    cur = "PLN"
    try:
        pa = node.get("price", {}).get("amount", {}) or {}
        price_units = int(float(pa.get("units", 0)))
        cur = pa.get("currencyCode") or pa.get("currency") or "PLN"
    except (ValueError, TypeError, AttributeError):
        pass
    if cur == "EUR":
        price_pln = round(price_units * EUR_TO_PLN)
        price_display = f"{price_units:,} EUR (~{price_pln:,} PLN)"
    else:
        price_pln = price_units
        price_display = f"{price_pln:,} PLN"

    photos = []
    thumb = node.get("thumbnail", {}) or {}
    if thumb.get("x2"):
        photos.append(thumb["x2"])
    elif thumb.get("x1"):
        photos.append(thumb["x1"])

    title = node.get("title", "") or (pd("make") + " " + pd("model")).strip() or "Unknown"
    url = node.get("url", "")

    return {
        "url": url,
        "title": title,
        "price_pln": price_pln,
        "price_display": price_display,
        "currency": cur,
        "year": year,
        "mileage": mileage,
        "fuel": pd("fuel_type") or pv("fuel_type"),
        "engine_cap": pv("engine_capacity") or pd("engine_capacity"),
        "power": re.sub(r"[^\d]", "", pv("engine_power") or pd("engine_power") or ""),
        "transmission": pd("gearbox") or pv("gearbox"),
        "color": pd("color"),
        "version": pd("version"),
        "body_type": pd("body_type"),
        "has_vin": False,
        "seller": "",
        "seller_type": "",
        "location": node.get("location", {}).get("city", "") if isinstance(node.get("location"), dict) else "",
        "generation_hint": pd("model") + " " + pd("version"),
        "photos": photos,
        "price_indicator": (node.get("priceEvaluation", {}) or {}).get("indicator"),
        "cepik": bool(node.get("cepikVerified")),
        "badges": [b for b in (node.get("badges") or []) if isinstance(b, str)],
    }


def fetch_search(search_url, max_pages=10, max_cars=120, deep=False):
    """Paste an otomoto SEARCH URL → pull every listing across result pages."""
    cars = []
    seen = set()
    base = search_url.split("#")[0]
    sep = "&" if "?" in base else "?"
    for page in range(1, max_pages + 1):
        page_url = base if page == 1 else f"{base}{sep}page={page}"
        try:
            data = _get_next_data(page_url)
        except Exception as e:
            print(f"  page {page}: fetch error {e}")
            break
        if not data:
            break
        try:
            urql = data["props"]["pageProps"]["urqlState"]
        except (KeyError, TypeError):
            print(f"  page {page}: no search data")
            break

        edges = None
        total = None
        for entry in urql.values():
            try:
                payload = json.loads(entry["data"]) if isinstance(entry.get("data"), str) else entry.get("data")
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict) and "advertSearch" in payload:
                adv = payload["advertSearch"]
                edges = adv.get("edges", [])
                total = adv.get("totalCount")
                break
        if not edges:
            break

        new_this_page = 0
        for edge in edges:
            node = edge.get("node", edge)
            url = node.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                car = build_car(_node_to_car(node))
                cars.append(car)
                new_this_page += 1
            except Exception as e:
                print(f"  skip node: {e}")
        print(f"  page {page}: +{new_this_page} cars (total so far {len(cars)}"
              + (f" / {total} listed)" if total else ")"))
        if len(cars) >= max_cars or new_this_page == 0:
            break
        time.sleep(0.6)

    cars = cars[:max_cars]
    if deep and cars:
        print(f"\nDeep scan: fetching detail page for {len(cars)} cars "
              f"(accident/import/sunroof/days-listed)...")
        enriched = []
        for i, c in enumerate(cars, 1):
            enriched.append(enrich_with_detail(c))
            if i % 10 == 0 or i == len(cars):
                print(f"  enriched {i}/{len(cars)}")
            time.sleep(0.4)
        cars = enriched
    return cars


EQUIPMENT_HIGHLIGHTS = {
    "sunroof": "Sunroof", "navigation_system": "Navigation", "leather_upholstery": "Leather",
    "upholstery_type": "Leather", "heated_seat_driver": "Heated seats",
    "rear_view_camera": "Rear camera", "360_view_camera": "360° camera",
    "park_assistant": "Park assist", "adaptive-cruise-control": "Adaptive cruise",
    "cruisecontrol_type": "Cruise control", "keyless_go": "Keyless go",
    "head_up_display": "Head-up display", "memory_seat": "Memory seats",
    "distance_control": "Distance control", "lane_control_assistant": "Lane assist",
    "led": "LED lights", "xenon": "Xenon lights",
}


def _days_since(iso_str):
    if not iso_str:
        return 0
    try:
        dt = datetime.strptime(iso_str[:10], "%Y-%m-%d")
        return max(0, (datetime.now() - dt).days)
    except (ValueError, TypeError):
        return 0


def _advert_to_raw(advert, url):
    """Extract a full raw dict from an otomoto detail-page advert object."""
    details = {d["key"]: d["value"] for d in advert.get("details", []) if "key" in d and "value" in d}
    pdict = advert.get("parametersDict", {}) or {}

    def param_val(key):
        v = pdict.get(key, {}).get("values")
        if v and isinstance(v, list):
            return v[0].get("value", "")
        return ""

    def is_yes(key):
        return param_val(key) == "1"

    price_raw = advert.get("price", {}) or {}
    currency = price_raw.get("currency") or (price_raw.get("amount", {}) or {}).get("currencyCode", "PLN")
    try:
        price_num = float(str(price_raw.get("value", "0")).replace(" ", "").replace("\xa0", ""))
    except (ValueError, TypeError):
        price_num = 0
    if not price_num:
        try:
            price_num = float((price_raw.get("amount", {}) or {}).get("units", 0))
        except (ValueError, TypeError):
            price_num = 0
    if currency == "EUR":
        price_pln = round(price_num * EUR_TO_PLN)
        price_display = f"{price_num:,.0f} EUR (~{price_pln:,} PLN)"
    else:
        price_pln = round(price_num)
        price_display = f"{price_pln:,} PLN"

    try:
        year = int(details.get("year", "0") or param_val("year") or 0)
    except (ValueError, TypeError):
        year = 0
    try:
        mileage = int(re.sub(r"[^\d]", "", str(details.get("mileage", "")) or param_val("mileage") or "0") or 0)
    except (ValueError, TypeError):
        mileage = 0

    vin_raw = param_val("vin") or details.get("vin", "")
    has_vin = is_yes("has_vin") or bool(vin_raw and len(vin_raw) > 10)

    seller = advert.get("seller", {}) or {}
    loc = seller.get("location", {}) or {}
    city = loc.get("city", "") or ""
    region = loc.get("region", "") or ""
    location = ", ".join(x for x in (city, region) if x)

    photos = []
    try:
        for p in (advert.get("images", {}) or {}).get("photos", [])[:12]:
            u = p.get("url") or (p.get("750x300") if isinstance(p, dict) else "")
            if u:
                photos.append(u)
    except (KeyError, TypeError, AttributeError):
        pass

    # equipment highlights (sunroof matters to wife, etc.)
    equip = []
    sunroof = bool(param_val("sunroof"))
    if sunroof:
        equip.append("Sunroof")
    for k, label in EQUIPMENT_HIGHLIGHTS.items():
        if k == "sunroof":
            continue
        if is_yes(k) or (param_val(k) and param_val(k) not in ("0", "")):
            if label not in equip:
                equip.append(label)
    # also scan equipment list array if present
    try:
        for grp in advert.get("equipment", []) or []:
            for item in grp.get("values", []) or []:
                lbl = item.get("label", "") if isinstance(item, dict) else str(item)
                low = lbl.lower()
                if "szyberdach" in low or "panoram" in low:
                    sunroof = True
                    if "Sunroof" not in equip:
                        equip.append("Sunroof")
    except (AttributeError, TypeError):
        pass

    created = advert.get("originalCreatedAt") or advert.get("createdAt") or ""
    days_on_market = _days_since(created)
    pd_obj = advert.get("priceDrop")
    price_dropped = bool(pd_obj)
    price_drop_amount = 0
    if isinstance(pd_obj, dict):
        try:
            price_drop_amount = int(float(pd_obj.get("amount", 0) or 0))
        except (ValueError, TypeError):
            pass

    price_indicator = (advert.get("priceEvaluation", {}) or {}).get("indicator")

    return {
        "url": url,
        "title": advert.get("title", "Unknown"),
        "price_pln": price_pln,
        "price_display": price_display,
        "currency": currency,
        "year": year,
        "mileage": mileage,
        "fuel": details.get("fuel_type", "") or pdict.get("fuel_type", {}).get("values", [{}])[0].get("label", ""),
        "engine_cap": details.get("engine_capacity", ""),
        "power": re.sub(r"[^\d]", "", details.get("engine_power", "") or param_val("engine_power") or ""),
        "transmission": details.get("gearbox", ""),
        "color": details.get("color", ""),
        "version": details.get("version", ""),
        "body_type": details.get("body_type", ""),
        "has_vin": has_vin,
        "seller": seller.get("name", ""),
        "seller_type": seller.get("type", ""),
        "location": location,
        "generation_hint": details.get("generation", "") + " " + details.get("version", ""),
        "photos": photos,
        "price_indicator": price_indicator,
        "cepik": bool(advert.get("cepikVerified") or advert.get("verifiedCar")),
        "badges": [b for b in (advert.get("badges") or []) if isinstance(b, str)],
        # rich deep-scan fields
        "damaged": is_yes("damaged"),
        "imported": is_yes("is_imported_car"),
        "rhd": param_val("rhd") == "1",  # 1 = right-hand drive
        "sunroof": sunroof,
        "has_dpf": is_yes("particle_filter"),
        "days_on_market": days_on_market,
        "price_dropped": price_dropped,
        "price_drop_amount": price_drop_amount,
        "equipment": equip,
        "description": (advert.get("description", "") or "")[:600],
        "deep": True,
        "source": "otomoto",
    }


def fetch_listing(url):
    try:
        data = _get_next_data(url)
    except Exception as e:
        return None, f"Failed to fetch URL: {e}"
    if not data:
        return None, "Could not find __NEXT_DATA__ in page"
    try:
        advert = data["props"]["pageProps"]["advert"]
    except (KeyError, TypeError):
        return None, "Listing data not found — paste a single listing URL, not a search page"
    if not advert:
        return None, "Empty advert data"
    return build_car(_advert_to_raw(advert, url)), None


def enrich_with_detail(car):
    """Fetch the detail page for a search-result car and merge rich fields in."""
    try:
        data = _get_next_data(car["url"])
        advert = data["props"]["pageProps"]["advert"]
        if not advert:
            return car
        return build_car(_advert_to_raw(advert, car["url"]))
    except Exception:
        return car  # keep the shallow node data on any failure


# ── HTML generation ───────────────────────────────────────────────────────

def build_nego_modal(car, idx):
    nego = car.get("nego") or {}
    target = nego.get("target", car["price"])
    leverage = nego.get("leverage", [])
    lev_html = "".join(f"<li>{l}</li>" for l in leverage) or "<li>Standard market negotiation room</li>"
    # ready-to-send Polish + English message
    msg = (f"Dzień dobry, jestem zainteresowany tym samochodem ({car['title']}). "
           f"Po przeanalizowaniu rynku i stanu auta mogę zaproponować {target:,} PLN. "
           f"Czy ta kwota jest do negocjacji? Chętnie umówię się na oględziny i jazdę próbną "
           f"z diagnostyką OBD. Pozdrawiam.").replace(",", " ")
    msg_js = msg.replace("'", "\\'").replace("\n", " ")
    return f"""
    <div id="nego{idx}" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:1000;overflow-y:auto">
      <div style="background:white;max-width:560px;margin:60px auto;border-radius:12px;padding:26px;position:relative">
        <button onclick="document.getElementById('nego{idx}').style.display='none'"
          style="position:absolute;top:12px;right:16px;background:none;border:none;font-size:20px;cursor:pointer">✕</button>
        <h3 style="margin-bottom:6px;font-size:17px">🤝 Negotiation plan</h3>
        <p style="color:#666;font-size:13px;margin-bottom:14px">{car['title']}</p>
        <div style="display:flex;gap:14px;margin-bottom:14px">
          <div style="flex:1;background:#f8f9fa;border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:11px;color:#888">ASKING</div>
            <div style="font-size:18px;font-weight:700">{car['price']:,}</div>
          </div>
          <div style="flex:1;background:#d1e7dd;border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:11px;color:#0a3622">YOUR OFFER</div>
            <div style="font-size:18px;font-weight:700;color:#0a3622">{target:,}</div>
          </div>
          <div style="flex:1;background:#fff3cd;border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:11px;color:#664d03">SAVE</div>
            <div style="font-size:18px;font-weight:700;color:#664d03">{nego.get('savings',0):,}</div>
          </div>
        </div>
        <h4 style="font-size:14px;margin-bottom:6px">Your leverage points:</h4>
        <ul style="padding-left:20px;color:#444;font-size:13px;margin-bottom:14px">{lev_html}</ul>
        <h4 style="font-size:14px;margin-bottom:6px">Ready-to-send message (Polish):</h4>
        <textarea id="negomsg{idx}" readonly style="width:100%;height:90px;font-size:13px;padding:8px;
          border:1px solid #dee2e6;border-radius:6px;resize:vertical">{msg}</textarea>
        <button onclick="navigator.clipboard.writeText(document.getElementById('negomsg{idx}').value);this.textContent='✓ Copied!'"
          style="margin-top:8px;background:#198754;color:white;border:none;padding:7px 14px;
          border-radius:6px;font-size:13px;cursor:pointer">📋 Copy message</button>
      </div>
    </div>"""


def severity_badge(s):
    cfg = {
        "CRITICAL": ("#dc3545", "#fff"),
        "HIGH":     ("#fd7e14", "#fff"),
        "MEDIUM":   ("#ffc107", "#000"),
        "LOW":      ("#6c757d", "#fff"),
    }
    bg, fg = cfg.get(s, ("#999", "#fff"))
    return (f'<span style="background:{bg};color:{fg};padding:2px 7px;border-radius:4px;'
            f'font-size:11px;font-weight:600;display:inline-block">{s}</span>')


def score_ring(score):
    color = "#28a745" if score >= 70 else ("#ffc107" if score >= 50 else "#dc3545")
    label = "Great" if score >= 75 else ("Good" if score >= 60 else ("Fair" if score >= 45 else "Avoid"))
    return f"""
    <div style="text-align:center">
      <svg width="68" height="68" viewBox="0 0 68 68">
        <circle cx="34" cy="34" r="28" fill="none" stroke="#e9ecef" stroke-width="8"/>
        <circle cx="34" cy="34" r="28" fill="none" stroke="{color}" stroke-width="8"
          stroke-dasharray="{round(score*1.759)} 176"
          stroke-dashoffset="44" stroke-linecap="round"/>
        <text x="34" y="33" text-anchor="middle" font-size="15" font-weight="700" fill="{color}">{score}</text>
        <text x="34" y="46" text-anchor="middle" font-size="9" fill="#888">{label}</text>
      </svg>
    </div>"""


def build_checklist_modal(car, idx):
    items = "".join(f'<li style="margin:6px 0;padding:6px 8px;background:#f8f9fa;border-radius:4px">{i}</li>'
                    for i in car["checklist"])
    q_items = "".join(f'<li style="margin:5px 0">{q}</li>' for q in QUESTIONS_FOR_SELLER)
    return f"""
    <div id="modal{idx}" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:1000;overflow-y:auto">
      <div style="background:white;max-width:640px;margin:40px auto;border-radius:12px;padding:28px;position:relative">
        <button onclick="document.getElementById('modal{idx}').style.display='none'"
          style="position:absolute;top:12px;right:16px;background:none;border:none;font-size:20px;cursor:pointer">✕</button>
        <h3 style="margin-bottom:16px;font-size:17px">📋 Test Drive Checklist — {car['title']}</h3>
        <p style="color:#666;font-size:13px;margin-bottom:12px">Engine: {car['engine_code']} · {car['generation']}</p>
        <ul style="list-style:none;padding:0">{items}</ul>
        <h4 style="margin:20px 0 10px;font-size:15px">❓ Questions to ask the seller</h4>
        <ul style="padding-left:20px;color:#444;font-size:13px">{q_items}</ul>
        <div style="margin-top:18px;padding:12px;background:#fff3cd;border-radius:8px;font-size:13px">
          ⚠️ Always get an independent OBD diagnostic scan before buying. Costs ~150–200 PLN and can save you thousands.
        </div>
      </div>
    </div>"""


def build_cost_bar(costs):
    total = costs["total"]
    parts = [
        ("Fuel", costs["fuel"], "#0d6efd"),
        ("Insurance", costs["insurance"], "#6f42c1"),
        ("Depreciation", costs["depreciation"], "#fd7e14"),
        ("Maintenance", costs["maintenance"], "#28a745"),
    ]
    bars = ""
    for label, val, color in parts:
        pct = round(val / total * 100) if total else 0
        bars += f"""
        <div style="display:flex;align-items:center;gap:6px;margin:3px 0;font-size:12px">
          <div style="width:80px;color:#666">{label}</div>
          <div style="flex:1;height:10px;background:#f0f0f0;border-radius:5px;overflow:hidden">
            <div style="width:{pct}%;height:100%;background:{color};border-radius:5px"></div>
          </div>
          <div style="width:55px;text-align:right;color:#333">{val:,} PLN</div>
        </div>"""
    return f"""
    <div style="margin-top:6px">
      <div style="font-weight:600;font-size:13px;margin-bottom:4px">~{total:,} PLN/month total</div>
      {bars}
      <div style="font-size:10px;color:#999;margin-top:3px">{costs['consumption']} L/100km est. · {AVG_KM_PER_MONTH:,} km/month</div>
    </div>"""


def price_indicator_badge(pi):
    cfg = {
        "BELOW": ("#28a745", "BELOW market", "Otomoto rates this priced under market"),
        "IN":    ("#0d6efd", "AT market", "Otomoto rates this in the normal range"),
        "ABOVE": ("#dc3545", "ABOVE market", "Otomoto rates this priced over market"),
    }
    if pi not in cfg:
        return ""
    bg, label, tip = cfg[pi]
    return (f'<span title="{tip}" style="background:{bg};color:#fff;padding:2px 7px;'
            f'border-radius:4px;font-size:10px;font-weight:700">📊 {label}</span>')


def cars_to_csv(cars):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Fit", "Score", "Title", "Year", "Mileage", "Price(PLN)", "MarketAvg",
                "vs Market %", "Otomoto", "SCT", "Transmission", "Engine", "Fuel", "Power",
                "Monthly", "3yr TCO", "Neg.Target", "CEPiK", "Location", "URL"])
    for c in sorted(cars, key=lambda x: x.get("fit", x["score"]), reverse=True):
        vs = ""
        if c["market_avg"]:
            vs = f"{(c['price']/c['market_avg']-1)*100:+.0f}"
        w.writerow([
            c.get("fit", ""), c["score"], c["title"], c["year"], c["mileage"], c["price"],
            c["market_avg"] or "", vs, c.get("price_indicator") or "",
            "YES" if c["sct_ok"] else "NO", c["transmission"], c["engine_code"], c["fuel"],
            c["power"], c["costs"]["total"], c["tco"]["total"],
            (c["nego"] or {}).get("target", ""), "YES" if c.get("cepik") else "",
            c["location"], c["url"],
        ])
    return buf.getvalue()


def build_html(cars):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    sorted_cars = sorted(cars, key=lambda x: x.get("fit", x["score"]), reverse=True)

    # Summary banner — best fit, best value, lowest risk
    winner = sorted_cars[0] if sorted_cars else None
    affordable = [c for c in cars if c["afford"]["price_color"] == "#28a745" and c["sct_ok"]]
    best_value = min(cars, key=lambda c: (c["price"] / c["market_avg"]) if c["market_avg"] else 99) if cars else None
    lowest_risk = min(cars, key=lambda c: sum(1 for s, _ in c["issues"] if s in ("CRITICAL","HIGH"))) if cars else None

    def banner_card(icon, label, car, note):
        if not car:
            return ""
        color = "#0d6efd" if label == "Top Scorer" else ("#28a745" if label == "Best Value" else "#6f42c1")
        return f"""
        <div style="background:white;border-radius:10px;padding:14px 18px;flex:1;min-width:200px;
          border-left:4px solid {color};box-shadow:0 2px 8px rgba(0,0,0,.07)">
          <div style="font-size:20px">{icon}</div>
          <div style="font-weight:700;color:{color};font-size:13px;margin:4px 0">{label}</div>
          <div style="font-size:14px;font-weight:600">{car['title'][:35]}</div>
          <div style="font-size:12px;color:#666;margin-top:2px">{note}</div>
        </div>"""

    banners = ""
    if winner:
        banners += banner_card("🎯", "Best Fit For You", winner,
                               f"Fit {winner.get('fit')}/100 · cond {winner['score']} · {winner['price_display']}")
    if best_value and best_value.get("market_avg"):
        diff_pct = round((best_value["price"] / best_value["market_avg"] - 1) * 100)
        banners += banner_card("💰", "Best Value", best_value, f"{diff_pct:+}% vs market · {best_value['price_display']}")
    if lowest_risk:
        crit_count = sum(1 for s, _ in lowest_risk["issues"] if s in ("CRITICAL", "HIGH"))
        banners += banner_card("🛡️", "Lowest Risk", lowest_risk, f"{crit_count} critical/high issues · {lowest_risk['price_display']}")

    rows = ""
    modals = ""
    for idx, c in enumerate(sorted_cars):
        issue_html = ""
        for sev, desc in c["issues"]:
            issue_html += f'<div style="margin:3px 0">{severity_badge(sev)} <span style="font-size:12px">{desc}</span></div>'
        if not issue_html:
            issue_html = '<span style="color:#28a745;font-size:13px">✓ No known flags</span>'

        sct_color = "#28a745" if c["sct_ok"] else "#dc3545"
        sct_icon = "✅" if c["sct_ok"] else "🚫"

        market_cell = '<span style="color:#aaa">—</span>'
        if c["market_avg"]:
            diff = c["price"] - c["market_avg"]
            pct = (diff / c["market_avg"]) * 100
            arrow = "▲" if diff > 0 else "▼"
            col = "#dc3545" if diff > 0 else "#28a745"
            market_cell = (f'<div style="color:#555;font-size:12px">{c["market_avg"]:,} PLN avg</div>'
                           f'<div style="color:{col};font-weight:600;font-size:13px">'
                           f'{arrow} {abs(diff):,} PLN ({pct:+.1f}%)</div>')

        photos_html = ""
        if c["photos"]:
            photos_html = (f'<img src="{c["photos"][0]}" style="width:180px;height:110px;'
                           f'object-fit:cover;border-radius:6px;display:block;margin-bottom:8px">')

        odo_html = ""
        if c["odo_flag"] == "LOW":
            odo_html = f'<div style="color:#856404;background:#fff3cd;padding:2px 6px;border-radius:4px;font-size:11px;margin-top:3px">⚠ {c["odo_note"]}</div>'
        elif c["odo_flag"] in ("HIGH", "ELEVATED"):
            odo_html = f'<div style="color:#842029;background:#f8d7da;padding:2px 6px;border-radius:4px;font-size:11px;margin-top:3px">📈 {c["odo_note"]}</div>'
        else:
            odo_html = f'<div style="color:#0a3622;background:#d1e7dd;padding:2px 6px;border-radius:4px;font-size:11px;margin-top:3px">✓ {c["odo_note"]}</div>'

        seller_badge = ""
        if c["seller_type"] == "PROFESSIONAL":
            seller_badge = '<span style="background:#e9ecef;color:#495057;padding:1px 5px;border-radius:3px;font-size:10px">DEALER</span>'
        elif c["seller_type"] == "PRIVATE":
            seller_badge = '<span style="background:#d1ecf1;color:#0c5460;padding:1px 5px;border-radius:3px;font-size:10px">PRIVATE</span>'

        vin_html = ('<span style="color:#28a745;font-size:11px">✓ VIN on record</span>' if c["has_vin"]
                    else '<span style="color:#aaa;font-size:11px">VIN not shown</span>')

        trans_l = (c.get("transmission") or "").lower()
        is_auto = "auto" in trans_l or "tronic" in trans_l or "dsg" in trans_l
        fuel_l = (c.get("fuel") or "").lower()
        fuel_class = "diesel" if ("diesel" in fuel_l or "cdi" in fuel_l) else ("hybrid" if "hybr" in fuel_l else "petrol")

        cepik_badge = ('<span style="background:#d1e7dd;color:#0a3622;padding:1px 6px;border-radius:3px;'
                       'font-size:10px;font-weight:600">🛡 CEPiK ✓</span>' if c.get("cepik") else "")
        pi_badge = price_indicator_badge(c.get("price_indicator"))
        _bmap = {"LOW_MILEAGE": "🛣 Low km", "FINANCING": "💳 Financing",
                 "DELIVERY": "🚚 Delivery", "WARRANTY": "✓ Warranty",
                 "VIDEO": "🎥 Video", "PROMOTED": "⭐ Promoted"}
        otomoto_badges = " ".join(
            f'<span style="background:#f1f3f5;color:#495057;padding:1px 6px;border-radius:3px;'
            f'font-size:10px">{_bmap.get(b, b.replace("_"," ").title())}</span>'
            for b in (c.get("badges") or [])[:3]
        )
        auto_badge = ('<span style="background:#cfe2ff;color:#084298;padding:1px 6px;border-radius:3px;'
                      'font-size:10px;font-weight:600">⚙ AUTO</span>' if is_auto else
                      '<span style="background:#f8d7da;color:#842029;padding:1px 6px;border-radius:3px;'
                      'font-size:10px;font-weight:600">MANUAL</span>')

        # deep-scan condition badges
        flag_badges = ""
        if c.get("damaged"):
            flag_badges += ('<span style="background:#dc3545;color:#fff;padding:1px 6px;border-radius:3px;'
                            'font-size:10px;font-weight:700">⚠ ACCIDENT</span> ')
        if c.get("rhd"):
            flag_badges += ('<span style="background:#842029;color:#fff;padding:1px 6px;border-radius:3px;'
                            'font-size:10px;font-weight:700">↔ RIGHT-HAND DRIVE</span> ')
        if c.get("imported"):
            flag_badges += ('<span style="background:#fff3cd;color:#664d03;padding:1px 6px;border-radius:3px;'
                            'font-size:10px;font-weight:600">🌍 Imported</span> ')
        if c.get("sunroof"):
            flag_badges += ('<span style="background:#cff4fc;color:#055160;padding:1px 6px;border-radius:3px;'
                            'font-size:10px;font-weight:600">☀ Sunroof</span> ')
        if c.get("deep") and not c.get("damaged"):
            flag_badges += ('<span style="background:#d1e7dd;color:#0a3622;padding:1px 6px;border-radius:3px;'
                            'font-size:10px;font-weight:600">✓ No accident declared</span> ')

        # days on market / price drop
        market_time_html = ""
        dom = c.get("days_on_market", 0)
        if dom >= 60:
            market_time_html = (f'<div style="font-size:11px;color:#664d03;background:#fff3cd;'
                                f'padding:2px 6px;border-radius:4px;margin-top:3px">🕐 Listed {dom} days — stale, negotiate hard</div>')
        elif dom >= 1:
            market_time_html = f'<div style="font-size:11px;color:#777;margin-top:3px">🕐 Listed {dom} days ago</div>'
        if c.get("price_dropped"):
            drop = c.get("price_drop_amount", 0)
            extra = f" (−{drop:,} PLN)" if drop else ""
            market_time_html += (f'<div style="font-size:11px;color:#0a3622;background:#d1e7dd;'
                                 f'padding:2px 6px;border-radius:4px;margin-top:3px">📉 Price already dropped{extra}</div>')

        # equipment highlights
        equip_html = ""
        if c.get("equipment"):
            chips = " ".join(
                f'<span style="background:#f1f3f5;color:#495057;padding:1px 6px;border-radius:3px;font-size:10px">{e}</span>'
                for e in c["equipment"][:8])
            equip_html = f'<div style="display:flex;gap:3px;flex-wrap:wrap;margin-top:6px">{chips}</div>'

        # affordability
        af = c["afford"]
        afford_html = (
            f'<div style="margin-top:6px;font-size:11px">'
            f'<div style="color:{af["price_color"]};font-weight:600">💵 {af["price_verdict"]}</div>'
            f'<div style="color:{af["run_color"]}">📉 {af["run_verdict"]} ({af["income_share"]}% of income)</div>'
            f'</div>'
        )
        # negotiation
        nego_html = ""
        if c["nego"] and c["nego"]["savings"] > 0:
            nego_html = (f'<div style="margin-top:5px;font-size:11px;color:#0a3622;background:#d1e7dd;'
                         f'padding:3px 6px;border-radius:4px;cursor:pointer" '
                         f'onclick="document.getElementById(\'nego{idx}\').style.display=\'flex\'">'
                         f'🤝 Offer ~{c["nego"]["target"]:,} PLN '
                         f'(-{c["nego"]["discount_pct"]}%, save {c["nego"]["savings"]:,}) · click for message</div>')
        # 3yr tco
        tco_html = (f'<div style="margin-top:5px;font-size:11px;color:#555">'
                    f'3-yr cost ~{c["tco"]["total"]:,} PLN ({c["tco"]["per_month"]:,}/mo)</div>')

        rows += f"""
        <tr data-fit="{c.get('fit', c['score'])}" data-score="{c['score']}" data-price="{c['price']}"
            data-year="{c['year']}" data-mileage="{c['mileage']}"
            data-sct="{1 if c['sct_ok'] else 0}" data-auto="{1 if is_auto else 0}" data-fuel="{fuel_class}"
            data-damaged="{1 if c.get('damaged') else 0}" data-sunroof="{1 if c.get('sunroof') else 0}"
            data-idx="{idx}">
          <td style="text-align:center;vertical-align:middle;min-width:90px">
            {score_ring(c.get('fit', c['score']))}
            <div style="font-size:9px;color:#999;margin-top:2px">FIT · cond {c['score']}</div>
            <button onclick="toggleStar({idx})" id="star{idx}" title="Add to shortlist"
              style="background:none;border:none;font-size:20px;cursor:pointer;margin-top:4px;opacity:.35">⭐</button>
          </td>
          <td style="min-width:220px">
            {photos_html}
            <a href="{c['url']}" target="_blank"
               style="font-weight:700;color:#0d6efd;text-decoration:none;font-size:14px">{c['title']}</a>
            <div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:5px">{flag_badges}</div>
            <div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:4px">{auto_badge} {cepik_badge} {pi_badge} {otomoto_badges}</div>
            <div style="font-size:12px;color:#666;margin-top:4px">
              {c['generation']} · {c['engine_code']} · {c.get('body_type','')}
            </div>
            <div style="font-size:12px;color:#666">{c['location']}</div>
            <div style="font-size:12px;margin-top:2px">{seller_badge} {c['seller'][:30]}</div>
            <div style="margin-top:4px">{vin_html}</div>
            {market_time_html}
            {equip_html}
            <div style="margin-top:6px">
              <button onclick="document.getElementById('modal{idx}').style.display='flex'"
                style="background:#0d6efd;color:white;border:none;padding:4px 10px;border-radius:5px;
                font-size:11px;cursor:pointer">📋 Test Drive Checklist</button>
            </div>
          </td>
          <td style="white-space:nowrap;vertical-align:top">
            <div style="font-weight:700;font-size:15px">{c['price_display']}</div>
            {afford_html}
            {nego_html}
            {tco_html}
          </td>
          <td style="vertical-align:top">{market_cell}</td>
          <td style="vertical-align:top">
            <div style="font-size:15px;font-weight:700">{c['year']}</div>
            <div style="color:#555;font-size:13px">{c['mileage']:,} km</div>
            {odo_html}
          </td>
          <td style="vertical-align:top;font-size:13px">
            <div style="font-weight:600">{c['fuel']}</div>
            <div style="color:#555">{c['engine_cap']}</div>
            <div style="color:#555">{c['power']} KM</div>
            <div style="color:#777">{c['transmission']}</div>
            <div style="color:#777">{c['color']}</div>
          </td>
          <td style="vertical-align:top">
            <div style="color:{sct_color};font-weight:700;font-size:13px">{sct_icon} {"COMPLIANT" if c['sct_ok'] else "NOT COMPLIANT"}</div>
            <div style="font-size:11px;color:#666;margin-top:3px">{c['sct_note']}</div>
          </td>
          <td style="vertical-align:top;min-width:240px">{issue_html}</td>
          <td style="vertical-align:top;min-width:200px">{build_cost_bar(c['costs'])}</td>
        </tr>"""

        modals += build_checklist_modal(c, idx)
        modals += build_nego_modal(c, idx)

    csv_data = cars_to_csv(cars).replace("\\", "\\\\").replace("`", "\\`").replace("</", "<\\/")
    sort_js = """
    function sortTable(col) {
      const tbody = document.querySelector('tbody');
      const rows = Array.from(tbody.querySelectorAll('tr'));
      const dir = tbody.dataset.dir === col ? -1 : 1;
      tbody.dataset.dir = dir === 1 ? col : '';
      const desc = (col === 'fit' || col === 'score' || col === 'year');
      rows.sort((a, b) => {
        let av = parseFloat(a.dataset[col]) || 0;
        let bv = parseFloat(b.dataset[col]) || 0;
        return desc ? (bv - av) * dir : (av - bv) * dir;
      });
      rows.forEach(r => tbody.appendChild(r));
    }
    function applyFilters() {
      const maxP = parseFloat(document.getElementById('fPrice').value) || Infinity;
      document.getElementById('fPriceLbl').textContent =
        maxP === Infinity ? 'Any' : maxP.toLocaleString() + ' PLN';
      const sctOnly = document.getElementById('fSct').checked;
      const autoOnly = document.getElementById('fAuto').checked;
      const noCrash = document.getElementById('fNoCrash').checked;
      const sunOnly = document.getElementById('fSun').checked;
      const fuel = document.getElementById('fFuel').value;
      let shown = 0;
      document.querySelectorAll('tbody tr').forEach(r => {
        let ok = true;
        if (parseFloat(r.dataset.price) > maxP) ok = false;
        if (sctOnly && r.dataset.sct !== '1') ok = false;
        if (autoOnly && r.dataset.auto !== '1') ok = false;
        if (noCrash && r.dataset.damaged === '1') ok = false;
        if (sunOnly && r.dataset.sunroof !== '1') ok = false;
        if (fuel !== 'all' && r.dataset.fuel !== fuel) ok = false;
        r.style.display = ok ? '' : 'none';
        if (ok) shown++;
      });
      document.getElementById('shownCount').textContent = shown;
    }
    function downloadCSV() {
      const csv = document.getElementById('csvData').textContent;
      const blob = new Blob([csv], {type: 'text/csv'});
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'mercedes_analysis.csv';
      a.click();
    }
    const starred = new Set();
    function toggleStar(idx) {
      const btn = document.getElementById('star' + idx);
      if (starred.has(idx)) { starred.delete(idx); btn.style.opacity = '.35'; }
      else { starred.add(idx); btn.style.opacity = '1'; }
      const bar = document.getElementById('shortlistBar');
      document.getElementById('starCount').textContent = starred.size;
      bar.style.display = starred.size ? 'flex' : 'none';
    }
    function showShortlistOnly() {
      document.querySelectorAll('tbody tr').forEach(r => {
        r.style.display = starred.has(parseInt(r.dataset.idx)) ? '' : 'none';
      });
    }
    function clearShortlist() {
      starred.forEach(i => { document.getElementById('star'+i).style.opacity='.35'; });
      starred.clear();
      document.getElementById('shortlistBar').style.display = 'none';
      applyFilters();
    }
    """

    th_style = "cursor:pointer;user-select:none"
    max_price = max((c["price"] for c in cars if c["price"] > 0), default=100000)
    slider_max = ((max_price // 5000) + 1) * 5000
    ins_mult = age_insurance_multiplier(PROFILE["age"], PROFILE["years_licensed"])
    profile_note = (f"Profile: age {PROFILE['age']} · budget {PROFILE['cash_budget']:,} "
                    f"(stretch {PROFILE['stretch_budget']:,}) PLN · insurance ×{ins_mult:.1f} (young driver) · "
                    f"{PROFILE['city']} daily · auto preferred")
    filter_bar = f"""
    <div style="background:white;border-radius:10px;padding:14px 18px;margin-bottom:16px;
      box-shadow:0 2px 8px rgba(0,0,0,.06);display:flex;gap:20px;flex-wrap:wrap;align-items:center">
      <div>
        <label style="font-size:12px;color:#666;display:block">Max price: <b id="fPriceLbl">Any</b></label>
        <input type="range" id="fPrice" min="10000" max="{slider_max}" step="1000" value="{slider_max}"
          oninput="applyFilters()" style="width:200px">
      </div>
      <label style="font-size:13px"><input type="checkbox" id="fSct" onchange="applyFilters()"> SCT compliant only</label>
      <label style="font-size:13px"><input type="checkbox" id="fAuto" onchange="applyFilters()"> Automatic only</label>
      <label style="font-size:13px"><input type="checkbox" id="fNoCrash" onchange="applyFilters()"> Hide accident cars</label>
      <label style="font-size:13px"><input type="checkbox" id="fSun" onchange="applyFilters()"> Sunroof only</label>
      <div>
        <label style="font-size:12px;color:#666">Fuel:
          <select id="fFuel" onchange="applyFilters()" style="font-size:13px;padding:2px">
            <option value="all">All</option>
            <option value="petrol">Petrol</option>
            <option value="diesel">Diesel</option>
            <option value="hybrid">Hybrid</option>
          </select>
        </label>
      </div>
      <div style="margin-left:auto;display:flex;gap:12px;align-items:center">
        <span style="font-size:13px;color:#666"><b id="shownCount">{len(cars)}</b> shown</span>
        <button onclick="downloadCSV()" style="background:#198754;color:white;border:none;
          padding:7px 14px;border-radius:6px;font-size:13px;cursor:pointer;font-weight:600">⬇ Export CSV</button>
      </div>
    </div>
    <div id="shortlistBar" style="display:none;background:#0f0c29;color:white;border-radius:10px;
      padding:12px 18px;margin-bottom:16px;align-items:center;gap:16px">
      <span style="font-weight:600">⭐ Shortlist: <b id="starCount">0</b> cars</span>
      <button onclick="showShortlistOnly()" style="background:#0d6efd;color:white;border:none;
        padding:6px 14px;border-radius:6px;font-size:13px;cursor:pointer">Show only shortlist</button>
      <button onclick="applyFilters()" style="background:#495057;color:white;border:none;
        padding:6px 14px;border-radius:6px;font-size:13px;cursor:pointer">Show all again</button>
      <button onclick="clearShortlist()" style="background:none;color:#aaa;border:1px solid #555;
        padding:6px 14px;border-radius:6px;font-size:13px;cursor:pointer">Clear</button>
    </div>
    <script type="text/plain" id="csvData">{csv_data}</script>"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mercedes Analyzer — {now}</title>
<style>
  * {{ box-sizing:border-box;margin:0;padding:0 }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f2f5;color:#212529 }}
  .header {{ background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;padding:24px 32px }}
  .header h1 {{ font-size:22px;font-weight:800;letter-spacing:-.3px }}
  .header p {{ font-size:13px;opacity:.65;margin-top:5px }}
  .container {{ padding:20px 28px }}
  .banners {{ display:flex;gap:14px;flex-wrap:wrap;margin-bottom:20px }}
  table {{ width:100%;border-collapse:collapse;background:white;border-radius:12px;
    overflow:hidden;box-shadow:0 2px 16px rgba(0,0,0,.08) }}
  th {{ background:#f8f9fa;padding:11px 13px;text-align:left;font-size:11px;
    text-transform:uppercase;letter-spacing:.5px;color:#6c757d;
    border-bottom:2px solid #dee2e6;{th_style} }}
  th:hover {{ background:#e9ecef;color:#333 }}
  td {{ padding:14px 13px;border-bottom:1px solid #f0f0f0;vertical-align:top }}
  tr:last-child td {{ border-bottom:none }}
  tr:hover td {{ background:#f8f9ff }}
  .footer {{ margin-top:20px;background:white;border-radius:10px;padding:16px 20px;
    box-shadow:0 2px 8px rgba(0,0,0,.06);font-size:13px;color:#555 }}
  .footer b {{ color:#333 }}
</style>
<script>{sort_js}</script>
</head>
<body>
<div class="header">
  <h1>🚗 Mercedes W204 / W205 — Car Analyzer v3</h1>
  <p>Generated {now} · Warsaw SCT 2026 · Click column headers to sort</p>
  <p style="margin-top:4px">{profile_note}</p>
</div>
<div class="container">
  <div class="banners">{banners}</div>
  {filter_bar}
  <table>
    <thead>
      <tr>
        <th onclick="sortTable('fit')">Fit ↕</th>
        <th>Listing</th>
        <th onclick="sortTable('price')">Price ↕</th>
        <th>vs Market</th>
        <th onclick="sortTable('mileage')">Year / KM ↕</th>
        <th>Engine</th>
        <th>Warsaw SCT</th>
        <th>Known Issues</th>
        <th>Monthly Cost</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <div class="footer">
    <b>Score:</b> 0–100 — factors age, mileage, price vs market, SCT status, known issues, odometer pattern. &nbsp;|&nbsp;
    <b>Monthly cost:</b> fuel + insurance + depreciation + maintenance estimate ({AVG_KM_PER_MONTH:,} km/month assumed). &nbsp;|&nbsp;
    <b>Warsaw SCT:</b> Diesel ≥ 2011 (Euro 5), Petrol ≥ 2005 (Euro 4) required. &nbsp;|&nbsp;
    <b>⚠ This tool assists — it does not replace a physical inspection or OBD scan.</b>
  </div>
</div>
{modals}
</body>
</html>"""


def _is_search_url(u):
    # search/listing pages contain /osobowe/ category paths without an offer id
    return ("otomoto.pl" in u and "/oferta/" not in u and "-ID" not in u)


def run(cars):
    if not cars:
        print("\nNo listings could be fetched. Check the URL(s) and try again.")
        return
    cars.sort(key=lambda x: x.get("fit", x["score"]), reverse=True)
    print(f"\nTop fits:")
    for c in cars[:5]:
        print(f"  Fit {c.get('fit')}/100 · {c['title'][:45]} · {c['price_display']} "
              f"· {'SCT✓' if c['sct_ok'] else 'SCT✗'}")
    html = build_html(cars)
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=".html", mode="w", encoding="utf-8", prefix="mercedes_v3_"
    )
    tmp.write(html)
    tmp.close()
    print(f"\n✓ Report ({len(cars)} cars): {tmp.name}")
    webbrowser.open(f"file:///{tmp.name.replace(os.sep, '/')}")
    print("Done.")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("\n=== Mercedes W204/W205 Otomoto Analyzer v3 ===")
    print("Choose mode:")
    print("  [1] SEARCH URL  — paste one otomoto search/results URL, auto-fetch every car")
    print("  [2] LISTINGS    — paste individual listing URLs (one per line)")
    choice = input("Mode (1/2, default 1): ").strip() or "1"

    cars = []
    if choice == "1":
        search_url = input("\nPaste otomoto search URL: ").strip()
        if "otomoto" not in search_url:
            print("Not an otomoto URL. Exiting.")
            return
        pages = input("How many result pages to scan? (default 5): ").strip()
        try:
            pages = int(pages)
        except ValueError:
            pages = 5
        deep_in = input("Deep scan each car for accident/import/sunroof/days-listed? "
                        "Slower but far more accurate (y/N): ").strip().lower()
        deep = deep_in in ("y", "yes")
        print(f"\nFetching up to {pages} pages{' + deep scan' if deep else ''}...")
        cars = fetch_search(search_url, max_pages=pages, deep=deep)
    else:
        print("\nPaste otomoto listing URLs one per line. Blank line when done.\n")
        urls = []
        while True:
            try:
                line = input("URL: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                break
            if "otomoto" in line:
                urls.append(line)
            else:
                print("  ⚠ Not an otomoto URL — skipped")
        for i, url in enumerate(urls, 1):
            print(f"\n[{i}/{len(urls)}] Fetching: {url[:72]}...")
            data, error = fetch_listing(url)
            if error:
                print(f"  ✗ {error}")
            else:
                print(f"  ✓ {data['title']} — {data['price_display']} — Fit: {data.get('fit')}/100")
                cars.append(data)

    run(cars)


if __name__ == "__main__":
    main()
