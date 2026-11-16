"""
International Cost of Capital App
=================================
Compares a project's cost of equity, cost of debt and WACC in USD and in the
currency of a selected country.

Data sources (fetched live, cached for 12 hours, refreshable from the sidebar):
  * Unlevered industry betas ........ Aswath Damodaran, betas.xls (US industries)
  * BBB (Baa2) USD default spread ... Aswath Damodaran, ratings.html
                                      (large non-financial service firms)
  * Corporate income tax rates ...... OECD Corporate Tax Statistics
                                      (combined statutory CIT rate, SDMX API)
  * 10-year government bond yields .. Trading Economics, /bonds

If a source cannot be reached, the app falls back to a clearly labeled
snapshot so it never breaks. Every input can also be overwritten manually.
"""

from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime, timezone

import altair as alt
import numpy as np
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------------------
# Page config and styling
# --------------------------------------------------------------------------------------
st.set_page_config(
    page_title="International Cost of Capital",
    page_icon="🌎",
    layout="wide",
    initial_sidebar_state="expanded",
)

NAVY = "#0F2A4A"
BLUE = "#1D5C8C"
TEAL = "#14A098"
AMBER = "#E0A100"
USD_COLOR = "#1D5C8C"
FX_COLOR = "#14A098"

st.markdown(
    f"""
    <style>
    .block-container {{padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1350px;}}
    .hero {{
        background: linear-gradient(135deg, {NAVY} 0%, {BLUE} 60%, {TEAL} 130%);
        color: #fff; padding: 1.4rem 1.8rem; border-radius: 18px; margin-bottom: 1.1rem;
        box-shadow: 0 6px 20px rgba(15,42,74,.18);
    }}
    .hero h1 {{color:#fff; font-size: 2.0rem; margin: 0 0 .25rem 0; font-weight: 750;}}
    .hero p  {{color:#E6F0F8; margin: 0; font-size: 1.02rem;}}
    .chip-row {{display:flex; flex-wrap:wrap; gap:.5rem; margin:.2rem 0 1rem 0;}}
    .chip {{
        background:#F3F6FA; border:1px solid #E1E8F0; border-radius: 999px;
        padding:.32rem .8rem; font-size:.86rem; color:{NAVY};
    }}
    .chip b {{font-weight:700;}}
    .card {{
        background:#FFFFFF; border:1px solid #E3E9F1; border-radius:16px;
        padding:1.1rem 1.35rem 1.0rem 1.35rem; box-shadow:0 2px 10px rgba(15,42,74,.06);
        height:100%;
    }}
    .card-usd {{border-top: 6px solid {USD_COLOR};}}
    .card-fx  {{border-top: 6px solid {FX_COLOR};}}
    .card h3 {{margin:0 0 .15rem 0; color:{NAVY}; font-size:1.25rem;}}
    .card .sub {{color:#5B6B7F; font-size:.88rem; margin-bottom:.6rem;}}
    .mrow {{display:flex; justify-content:space-between; align-items:baseline;
            padding:.42rem 0; border-bottom:1px dashed #E3E9F1; font-size:.98rem;}}
    .mrow:last-child {{border-bottom:none;}}
    .mrow .lbl {{color:#3A4A5E;}}
    .mrow .val {{font-weight:650; color:{NAVY}; font-variant-numeric: tabular-nums;}}
    .mrow.big .lbl {{font-weight:700; color:{NAVY};}}
    .mrow.big .val {{font-size:1.45rem; font-weight:800;}}
    .mrow.big.usd .val {{color:{USD_COLOR};}}
    .mrow.big.fx .val  {{color:{FX_COLOR};}}
    .badge {{display:inline-block; padding:.08rem .5rem; border-radius:999px;
             font-size:.72rem; font-weight:700; margin-left:.35rem; vertical-align:middle;}}
    .b-live {{background:#E3F6EC; color:#127A46;}}
    .b-fallback {{background:#FFF4E0; color:#A15C00;}}
    .b-manual {{background:#E8EEFC; color:#2B4FB3;}}
    .b-calc {{background:#F0ECFB; color:#5B3DB5;}}
    .note {{color:#5B6B7F; font-size:.86rem;}}
    div[data-testid="stMetricValue"] {{font-variant-numeric: tabular-nums;}}
    section[data-testid="stSidebar"] .stButton>button {{width:100%; font-weight:650;}}
    </style>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------------------
# Constants: sources and fallbacks
# --------------------------------------------------------------------------------------
BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
RATINGS_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"
OECD_PAGE_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html#chapter-d1e2342-08e74a33fb"
)
OECD_API_URL = "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all"
TE_BONDS_URL = "https://tradingeconomics.com/bonds"
TE_COUNTRY_URL = "https://tradingeconomics.com/{slug}/government-bond-yield"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
DEFAULT_ERP = 6.5   # %
DEFAULT_DEBT = 30   # %

# Fallback snapshot of Damodaran's US unlevered betas (January 2026 update).
# Used ONLY if betas.xls cannot be downloaded. Software (System & Application) is
# an estimate (corrected-for-cash beta of 1.25 adjusted for cash); refresh for exact data.
FALLBACK_BETAS = {
    "Advertising": 0.93, "Aerospace/Defense": 0.85, "Air Transport": 0.70, "Apparel": 0.76,
    "Auto & Truck": 1.27, "Auto Parts": 1.02, "Bank (Money Center)": 0.34,
    "Banks (Regional)": 0.29, "Cable TV": 0.35, "Chemical (Basic)": 0.58,
    "Chemical (Diversified)": 0.37, "Chemical (Specialty)": 0.79,
    "Coal & Related Energy": 1.02, "Computer Services": 0.92,
    "Computers/Peripherals": 1.31, "Construction Supplies": 1.02, "Diversified": 0.79,
    "Drugs (Biotechnology)": 1.03, "Drugs (Pharmaceutical)": 0.89, "Education": 0.66,
    "Electrical Equipment": 1.15, "Entertainment": 0.74,
    "Environmental & Waste Services": 0.81, "Farming/Agriculture": 0.81,
    "Financial Svcs. (Non-bank & Insurance)": 0.32, "Food Processing": 0.46,
    "Food Wholesalers": 0.64, "Furn/Home Furnishings": 0.62,
    "Green & Renewable Energy": 0.46, "Healthcare Products": 0.83,
    "Healthcare Support Services": 0.69, "Heathcare Information and Technology": 0.99,
    "Insurance (General)": 0.56, "Insurance (Life)": 0.43, "Insurance (Prop/Cas.)": 0.44,
    "Investments & Asset Management": 0.53, "Machinery": 0.87, "Metals & Mining": 0.96,
    "Office Equipment & Services": 0.98, "Oil/Gas (Integrated)": 0.27,
    "Oil/Gas (Production and Exploration)": 0.56, "Oil/Gas Distribution": 0.47,
    "Oilfield Svcs/Equip.": 0.74, "Packaging & Container": 0.72,
    "Paper/Forest Products": 0.72, "Power": 0.31, "Recreation": 0.70, "Reinsurance": 0.44,
    "Restaurant/Dining": 0.77, "Retail (Automotive)": 0.70,
    "Retail (Building Supply)": 1.31, "Retail (Distributors)": 0.78,
    "Retail (General)": 0.76, "Retail (Grocery and Food)": 0.80, "Retail (REITs)": 0.44,
    "Retail (Special Lines)": 0.95, "Software (System & Application)": 1.23,
}
FALLBACK_BETAS_DATE = "January 2026 (offline snapshot)"

FALLBACK_BBB_SPREAD = 0.0111
FALLBACK_BBB_DATE = "January 2026 (offline snapshot)"

# Fallback OECD combined statutory CIT rates (approximately 2024). Used ONLY if the OECD API fails.
FALLBACK_TAX = {
    "Argentina": 35.0, "Australia": 30.0, "Austria": 23.0, "Belgium": 25.0, "Brazil": 34.0,
    "Canada": 26.1, "Chile": 27.0, "China": 25.0, "Colombia": 35.0, "Costa Rica": 30.0,
    "Czech Republic": 21.0, "Denmark": 22.0, "Egypt": 22.5, "Estonia": 20.0, "Finland": 20.0,
    "France": 25.8, "Germany": 29.9, "Greece": 22.0, "Hong Kong": 16.5, "Hungary": 9.0,
    "Iceland": 20.0, "India": 25.2, "Indonesia": 22.0, "Ireland": 12.5, "Israel": 23.0,
    "Italy": 27.8, "Japan": 29.7, "Kenya": 30.0, "South Korea": 26.4, "Latvia": 20.0,
    "Lithuania": 15.0, "Luxembourg": 24.9, "Malaysia": 24.0, "Mexico": 30.0,
    "Netherlands": 25.8, "New Zealand": 28.0, "Nigeria": 30.0, "Norway": 22.0,
    "Peru": 29.5, "Philippines": 25.0, "Poland": 19.0, "Portugal": 31.5, "Romania": 16.0,
    "Saudi Arabia": 20.0, "Singapore": 17.0, "Slovakia": 21.0, "Slovenia": 22.0,
    "South Africa": 27.0, "Spain": 25.0, "Sweden": 20.6, "Switzerland": 19.6,
    "Thailand": 20.0, "Turkey": 25.0, "United Kingdom": 25.0, "United States": 25.6,
    "Vietnam": 20.0,
}
FALLBACK_TAX_DATE = "2024 (offline snapshot of OECD Corporate Tax Statistics)"

# Fallback 10Y yields: rough, NOT live. Only used if Trading Economics cannot be reached.
FALLBACK_YIELDS = {
    "United States": 4.10, "Mexico": 9.00, "Brazil": 13.50, "Canada": 3.20, "Germany": 2.60,
    "United Kingdom": 4.50, "France": 3.40, "Italy": 3.50, "Spain": 3.20, "Japan": 1.70,
    "China": 1.80, "India": 6.50, "Australia": 4.40, "South Korea": 3.00, "Switzerland": 0.30,
    "Colombia": 11.50, "Chile": 5.80, "Peru": 6.50, "South Africa": 9.50, "Indonesia": 6.50,
    "Turkey": 30.00, "Netherlands": 2.80, "Poland": 5.50, "Sweden": 2.50, "Norway": 3.90,
}
FALLBACK_YIELDS_DATE = "Offline approximation (not live, please refresh or override)"

CURRENCY = {
    "united states": "USD", "mexico": "MXN", "brazil": "BRL", "canada": "CAD",
    "united kingdom": "GBP", "japan": "JPY", "china": "CNY", "india": "INR",
    "south korea": "KRW", "australia": "AUD", "new zealand": "NZD", "switzerland": "CHF",
    "sweden": "SEK", "norway": "NOK", "denmark": "DKK", "poland": "PLN",
    "czech republic": "CZK", "hungary": "HUF", "turkey": "TRY", "south africa": "ZAR",
    "chile": "CLP", "colombia": "COP", "peru": "PEN", "argentina": "ARS",
    "indonesia": "IDR", "malaysia": "MYR", "philippines": "PHP", "thailand": "THB",
    "vietnam": "VND", "singapore": "SGD", "hong kong": "HKD", "israel": "ILS",
    "russia": "RUB", "egypt": "EGP", "nigeria": "NGN", "kenya": "KES",
    "saudi arabia": "SAR", "pakistan": "PKR", "romania": "RON", "bulgaria": "BGN",
    "serbia": "RSD", "iceland": "ISK", "taiwan": "TWD", "morocco": "MAD",
    "uruguay": "UYU", "costa rica": "CRC", "bangladesh": "BDT", "sri lanka": "LKR",
    "ukraine": "UAH", "kazakhstan": "KZT", "qatar": "QAR", "uganda": "UGX",
    "zambia": "ZMW", "namibia": "NAD", "botswana": "BWP", "mauritius": "MUR",
    "jordan": "JOD", "bahrain": "BHD", "oman": "OMR", "dominican republic": "DOP",
    "jamaica": "JMD", "panama": "USD", "ecuador": "USD", "el salvador": "USD",
}
EURO = {
    "germany", "france", "italy", "spain", "netherlands", "belgium", "austria", "portugal",
    "ireland", "finland", "greece", "slovakia", "slovenia", "lithuania", "latvia", "estonia",
    "luxembourg", "cyprus", "malta", "croatia",
}

ALIASES = {
    "turkiye": "turkey", "korea": "south korea", "republic of korea": "south korea",
    "czechia": "czech republic", "slovak republic": "slovakia",
    "russian federation": "russia", "viet nam": "vietnam",
    "united states of america": "united states", "cote d'ivoire": "ivory coast",
    "chinese taipei": "taiwan", "people's republic of china": "china",
}
DISPLAY = {"turkey": "Türkiye", "south korea": "South Korea", "czech republic": "Czech Republic"}
NOT_COUNTRIES = {"euro area", "european union", "world"}


def canon(name: str) -> str:
    """Normalize a country name so OECD and Trading Economics names can be matched."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = re.sub(r"\(.*?\)", "", s).replace("-", " ").lower().strip()
    s = re.sub(r"\s+", " ", s)
    return ALIASES.get(s, s)


def display_name(key: str) -> str:
    return DISPLAY.get(key, " ".join(w if w in {"and", "of"} else w.capitalize() for w in key.split()))


def currency_of(key: str) -> str:
    if key in EURO:
        return "EUR"
    return CURRENCY.get(key, "LCY")


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _to_float(x) -> float | None:
    try:
        if isinstance(x, str):
            x = x.replace("%", "").replace(",", "").strip()
        v = float(x)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------------------
# Data fetchers (cached; "Refresh data" clears the cache)
# --------------------------------------------------------------------------------------
@st.cache_data(ttl=12 * 3600, show_spinner=False)
def fetch_betas() -> dict:
    """Damodaran US industry betas, 'Unlevered beta' column of the Industry Averages sheet."""
    try:
        r = requests.get(BETAS_URL, headers=BROWSER_HEADERS, timeout=40)
        r.raise_for_status()
        xls = pd.ExcelFile(io.BytesIO(r.content), engine="xlrd")
        sheets = [s for s in xls.sheet_names if "industry" in s.lower()] or xls.sheet_names
        for sheet in sheets:
            raw = pd.read_excel(xls, sheet_name=sheet, header=None)
            hdr = None
            for i in range(min(60, len(raw))):
                cells = raw.iloc[i].astype(str).str.strip().str.lower()
                if (cells == "industry name").any() and cells.str.startswith("unlevered beta").any():
                    hdr = i
                    break
            if hdr is None:
                continue
            cols = [str(c).strip() for c in raw.iloc[hdr]]
            df = raw.iloc[hdr + 1:].copy()
            df.columns = cols
            low = [c.lower() for c in cols]
            name_c = cols[low.index("industry name")]
            ub_c = cols[low.index("unlevered beta")] if "unlevered beta" in low else \
                next(c for c in cols if c.lower().startswith("unlevered beta"))
            extra = {}
            for lbl, key in [("number of firms", "Firms"), ("beta", "Levered beta (Damodaran)"),
                             ("d/e ratio", "D/E (Damodaran)")]:
                if lbl in low:
                    extra[cols[low.index(lbl)]] = key
            out = df[[name_c, ub_c] + list(extra)].rename(
                columns={name_c: "Industry", ub_c: "Unlevered beta", **extra})
            out["Industry"] = out["Industry"].astype(str).str.strip()
            out["Unlevered beta"] = pd.to_numeric(out["Unlevered beta"], errors="coerce")
            out = out[(out["Industry"] != "nan") & out["Unlevered beta"].notna()]
            out = out.reset_index(drop=True)
            if len(out) < 10:
                continue
            # Find the "Date updated" stamp in the rows above the header
            as_of = None
            top = raw.iloc[:hdr]
            for _, row in top.iterrows():
                vals = list(row.values)
                for j, v in enumerate(vals):
                    if isinstance(v, str) and "date" in v.lower():
                        for w in vals[j + 1:]:
                            if isinstance(w, (datetime, pd.Timestamp)):
                                as_of = pd.Timestamp(w).strftime("%B %d, %Y")
                                break
                            if isinstance(w, str) and re.search(r"\d", w):
                                as_of = w.strip()
                                break
                        m = re.search(r"(\d{1,2}[-/ ][A-Za-z]{3}[-/ ]\d{2,4}|[A-Z][a-z]+ \d{4})", v)
                        if not as_of and m:
                            as_of = m.group(1)
                    if as_of:
                        break
                if as_of:
                    break
            return {"df": out, "status": "Live", "as_of": as_of or "See source file",
                    "fetched": now_utc(), "error": None}
        raise ValueError("Could not find the 'Industry Name / Unlevered beta' table in betas.xls")
    except Exception as e:  # noqa: BLE001
        df = pd.DataFrame({"Industry": list(FALLBACK_BETAS), "Unlevered beta": list(FALLBACK_BETAS.values())})
        return {"df": df, "status": "Fallback", "as_of": FALLBACK_BETAS_DATE,
                "fetched": now_utc(), "error": str(e)}


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def fetch_bbb_spread() -> dict:
    """Damodaran synthetic rating table: spread for Baa2/BBB, large non-financial service firms."""
    try:
        r = requests.get(RATINGS_URL, headers=BROWSER_HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        spread = None
        for tr in soup.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            for j, c in enumerate(cells):
                if re.sub(r"\s", "", c).lower() == "baa2/bbb":
                    # first percentage after the FIRST Baa2/BBB cell = large non-financial firms
                    for k in range(j + 1, len(cells)):
                        m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", cells[k])
                        if m:
                            spread = float(m.group(1)) / 100
                            break
                    break
            if spread is not None:
                break
        if spread is None:
            raise ValueError("Baa2/BBB row not found")
        text = soup.get_text(" ", strip=True)
        m = re.search(r"(?:as of|Updated in|updated)\s+([A-Z][a-z]+\s+\d{4})", text)
        return {"value": spread, "status": "Live", "as_of": m.group(1) if m else "See source page",
                "fetched": now_utc(), "error": None}
    except Exception as e:  # noqa: BLE001
        return {"value": FALLBACK_BBB_SPREAD, "status": "Fallback", "as_of": FALLBACK_BBB_DATE,
                "fetched": now_utc(), "error": str(e)}


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def fetch_tax_rates() -> dict:
    """OECD combined (central + sub-central) statutory corporate income tax rates, latest year."""
    try:
        params = {"startPeriod": "2018", "dimensionAtObservation": "AllDimensions",
                  "format": "csvfilewithlabels"}
        r = requests.get(OECD_API_URL, params=params,
                         headers={"User-Agent": BROWSER_HEADERS["User-Agent"], "Accept": "text/csv"},
                         timeout=60)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text), low_memory=False)
        if "OBS_VALUE" not in df.columns or "TIME_PERIOD" not in df.columns:
            raise ValueError("Unexpected OECD response format")
        cols = list(df.columns)
        # Country label = column right after REF_AREA (csvfilewithlabels layout), else REF_AREA
        area_c = "REF_AREA"
        label_c = cols[cols.index(area_c) + 1] if area_c in cols and cols.index(area_c) + 1 < len(cols) else area_c
        if label_c in ("OBS_VALUE", "TIME_PERIOD"):
            label_c = area_c
        df["value"] = pd.to_numeric(df["OBS_VALUE"], errors="coerce")
        df["year"] = pd.to_numeric(df["TIME_PERIOD"].astype(str).str[:4], errors="coerce")
        df = df.dropna(subset=["value", "year"])
        skip = {area_c, label_c, "OBS_VALUE", "TIME_PERIOD", "value", "year"}
        desc_cols = [c for c in cols if c not in skip and df[c].dtype == object]
        df["desc"] = df[desc_cols].astype(str).agg(" | ".join, axis=1).str.lower()
        bad = r"targeted|small|reduced|sub-central|subcentral|central government|exclusive|surtax|special"
        comb = df[df["desc"].str.contains("combined") & ~df["desc"].str.contains("targeted|small|reduced")]
        if comb.empty:
            # Combined rate is the highest standard (non-targeted) rate per country-year
            comb = df[~df["desc"].str.contains(bad)]
            if comb.empty:
                comb = df
        comb = comb.sort_values("value").groupby([label_c, "year"], as_index=False)["value"].max()
        latest = comb.sort_values("year").groupby(label_c, as_index=False).last()
        rates, years = {}, {}
        for _, row in latest.iterrows():
            k = canon(row[label_c])
            v = float(row["value"])
            rates[k] = v / 100 if v > 1 else v
            years[k] = int(row["year"])
        if len(rates) < 20:
            raise ValueError("Too few countries returned by OECD")
        main_year = pd.Series(list(years.values())).mode().iloc[0]
        return {"rates": rates, "years": years, "status": "Live",
                "as_of": f"{main_year} (latest year in OECD database)", "fetched": now_utc(), "error": None}
    except Exception as e:  # noqa: BLE001
        rates = {canon(k): v / 100 for k, v in FALLBACK_TAX.items()}
        return {"rates": rates, "years": {k: 2024 for k in rates}, "status": "Fallback",
                "as_of": FALLBACK_TAX_DATE, "fetched": now_utc(), "error": str(e)}


def _parse_te_bonds(html: str) -> dict:
    """Parse the Trading Economics /bonds table: country -> (yield %, date text)."""
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for a in soup.find_all("a", href=True):
        m = re.match(r"^/?([a-z\-]+)/government-bond-yield/?$", a["href"].strip().lower())
        if not m:
            continue
        tr = a.find_parent("tr")
        if tr is None:
            continue
        tds = tr.find_all("td")
        idx = next((i for i, td in enumerate(tds) if a in td.descendants), 0)
        y = None
        for td in tds[idx + 1:]:
            y = _to_float(td.get_text(strip=True))
            if y is not None:
                break
        if y is None:
            continue
        date_txt = tds[-1].get_text(strip=True) if tds else ""
        key = canon(m.group(1).replace("-", " "))
        if key in NOT_COUNTRIES:
            continue
        out.setdefault(key, {"yield": y / 100, "date": date_txt, "slug": m.group(1)})
    return out


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_yields() -> dict:
    """10-year government bond yields from Trading Economics (scraped, no API key)."""
    try:
        r = requests.get(TE_BONDS_URL, headers=BROWSER_HEADERS, timeout=30)
        r.raise_for_status()
        data = _parse_te_bonds(r.text)
        if len(data) < 10:
            raise ValueError("Bond table not found in Trading Economics page")
        return {"data": data, "status": "Live", "as_of": f"Scraped {now_utc()}",
                "fetched": now_utc(), "error": None}
    except Exception as e:  # noqa: BLE001
        data = {canon(k): {"yield": v / 100, "date": "offline", "slug": canon(k).replace(" ", "-")}
                for k, v in FALLBACK_YIELDS.items()}
        return {"data": data, "status": "Fallback", "as_of": FALLBACK_YIELDS_DATE,
                "fetched": now_utc(), "error": str(e)}


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_te_country(slug: str) -> dict | None:
    """Second attempt for one country: its own Trading Economics bond page."""
    try:
        r = requests.get(TE_COUNTRY_URL.format(slug=slug), headers=BROWSER_HEADERS, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
        text = meta["content"] if meta and meta.get("content") else soup.get_text(" ", strip=True)[:3000]
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", text)
        if not m:
            return None
        d = re.search(r"on ([A-Z][a-z]+ \d{1,2}, \d{4})", text)
        return {"yield": float(m.group(1)) / 100, "date": d.group(1) if d else now_utc(), "slug": slug}
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------------------
# Core finance
# --------------------------------------------------------------------------------------
def compute(beta_u, tax, debt_share, erp, rf_us, rf_fx, spread_us,
            spread_fx_override=None, method="parity") -> dict:
    """
    All rates as decimals.
      Levered beta       bL = bU x [1 + (1 - t) x D/E],   D/E = w_d / (1 - w_d)
      USD cost of equity  ke$ = rf$ + bL x ERP
      USD cost of debt    kd$ = rf$ + BBB spread$
      Foreign (parity)    k_fx = (1 + k$) x (1 + rf_fx) / (1 + rf$) - 1   for ke and kd
      Foreign (additive)  ke_fx = rf_fx + bL x ERP ; kd_fx = rf_fx + spread$
      Override            kd_fx = rf_fx + manual foreign spread
      WACC                (1 - w_d) x ke + w_d x kd x (1 - t)
    """
    w_d = debt_share
    w_e = 1 - w_d
    de = w_d / w_e if w_e > 0 else np.inf
    beta_l = beta_u * (1 + (1 - tax) * de)

    ke_us = rf_us + beta_l * erp
    kd_us = rf_us + spread_us

    if method == "parity":
        ratio = (1 + rf_fx) / (1 + rf_us)
        ke_fx = (1 + ke_us) * ratio - 1
        kd_fx_auto = (1 + kd_us) * ratio - 1
    else:
        ke_fx = rf_fx + beta_l * erp
        kd_fx_auto = rf_fx + spread_us
    spread_fx_auto = kd_fx_auto - rf_fx

    if spread_fx_override is not None:
        spread_fx = spread_fx_override
        kd_fx = rf_fx + spread_fx_override
    else:
        spread_fx = spread_fx_auto
        kd_fx = kd_fx_auto

    wacc_us = w_e * ke_us + w_d * kd_us * (1 - tax)
    wacc_fx = w_e * ke_fx + w_d * kd_fx * (1 - tax)
    return {
        "de": de, "beta_l": beta_l,
        "rf_us": rf_us, "rf_fx": rf_fx, "spread_us": spread_us,
        "spread_fx": spread_fx, "spread_fx_auto": spread_fx_auto,
        "ke_us": ke_us, "ke_fx": ke_fx,
        "kd_us": kd_us, "kd_fx": kd_fx,
        "kd_us_at": kd_us * (1 - tax), "kd_fx_at": kd_fx * (1 - tax),
        "wacc_us": wacc_us, "wacc_fx": wacc_fx,
    }


def pct(x, d=2) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{x * 100:.{d}f}%"


def badge(status: str) -> str:
    cls = {"Live": "b-live", "Fallback": "b-fallback", "Manual": "b-manual", "Calculated": "b-calc"}.get(status, "b-calc")
    return f'<span class="badge {cls}">{status}</span>'


# --------------------------------------------------------------------------------------
# Load data
# --------------------------------------------------------------------------------------
with st.spinner("Fetching the latest data from Damodaran, OECD and Trading Economics..."):
    betas = fetch_betas()
    bbb = fetch_bbb_spread()
    taxes = fetch_tax_rates()
    ylds = fetch_yields()

beta_df = betas["df"]
tax_rates = taxes["rates"]
yield_data = dict(ylds["data"])

# Country universe = countries with a 10Y yield (live or fallback), plus the US
country_keys = sorted(set(yield_data) | {"united states"}, key=lambda k: display_name(k))
country_labels = {k: display_name(k) for k in country_keys}

# --------------------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### 🌎 Cost of Capital")
    if st.button("🔄 Refresh data", help="Clear the cache and fetch every source again"):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"Last fetch: {betas['fetched']}")

    st.markdown("#### 1 · Project setup")
    industries = beta_df["Industry"].tolist()
    ind_default = next((i for i, n in enumerate(industries) if n.lower().startswith("software (system")), 0)
    industry = st.selectbox("Industry", industries, index=ind_default,
                            help="US industry groupings from Damodaran's betas.xls")

    c_default = country_keys.index("united states") if "united states" in country_keys else 0
    ckey = st.selectbox("Target country", country_keys, index=c_default,
                        format_func=lambda k: country_labels[k],
                        help="The foreign currency is the currency of this country")
    cname = country_labels[ckey]
    ccy = currency_of(ckey)
    fx_lbl = ccy if ccy != "USD" else "USD (target)"  # column label for the foreign currency

    erp = st.slider("Equity risk premium (%)", 0.0, 15.0, DEFAULT_ERP, 0.1,
                    help="Mature market (USD) equity risk premium") / 100
    debt_share = st.slider("Target debt share D/(D+E) (%)", 0, 90, DEFAULT_DEBT, 1,
                           help="Weight of debt in the capital structure") / 100

    tax_choice = st.radio("Project tax rate", ["U.S.", cname if ckey != "united states" else "Selected country"],
                          horizontal=True, index=0,
                          help="The same tax rate is applied to both currencies")
    use_us_tax = tax_choice == "U.S."

    with st.expander("⚙️ Advanced: currency conversion"):
        method_lbl = st.radio(
            "Method",
            ["Interest rate parity (multiplicative)", "Additive yield differential"],
            index=0,
            help="Parity: (1+k_fx) = (1+k$) x (1+rf_fx)/(1+rf$). Additive: swap the US risk free rate "
                 "for the foreign one.",
        )
        method = "parity" if method_lbl.startswith("Interest") else "additive"

    st.markdown("#### 2 · Manual overrides")
    st.caption("Switch on any toggle to overwrite the fetched value.")

    # Fetched defaults
    beta_fetched = float(beta_df.loc[beta_df["Industry"] == industry, "Unlevered beta"].iloc[0])
    tax_us_fetched = tax_rates.get("united states")
    tax_fx_fetched = tax_rates.get(ckey)

    rf_us_info = yield_data.get("united states")
    if ylds["status"] == "Live" and rf_us_info is None:
        rf_us_info = fetch_te_country("united-states")
    rf_fx_info = yield_data.get(ckey)
    if ylds["status"] == "Live" and rf_fx_info is None and ckey != "united states":
        rf_fx_info = fetch_te_country(ckey.replace(" ", "-"))

    o_beta = st.toggle("Unlevered beta", key="t_beta")
    beta_u = st.number_input("Unlevered beta", 0.0, 5.0, round(beta_fetched, 4), 0.01, format="%.4f",
                             key=f"beta_{industry}") if o_beta else beta_fetched

    o_tax = st.toggle("Corporate tax rates", key="t_tax")
    if o_tax:
        tax_us = st.number_input("U.S. tax rate (%)", 0.0, 60.0,
                                 round((tax_us_fetched or 0.25) * 100, 2), 0.1, key="tax_us") / 100
        tax_fx = st.number_input(f"{cname} tax rate (%)", 0.0, 60.0,
                                 round((tax_fx_fetched if tax_fx_fetched is not None else tax_us) * 100, 2),
                                 0.1, key=f"tax_fx_{ckey}") / 100
    else:
        tax_us = tax_us_fetched if tax_us_fetched is not None else FALLBACK_TAX["United States"] / 100
        tax_fx = tax_fx_fetched if tax_fx_fetched is not None else tax_us

    o_spread = st.toggle("USD BBB spread", key="t_spread")
    spread_us = st.number_input("USD BBB spread (%)", 0.0, 10.0, round(bbb["value"] * 100, 2), 0.01,
                                key="spread_us") / 100 if o_spread else bbb["value"]

    o_rf = st.toggle("10-year government yields", key="t_rf")
    rf_us_fetched = rf_us_info["yield"] if rf_us_info else FALLBACK_YIELDS["United States"] / 100
    rf_fx_fetched = rf_us_fetched if ckey == "united states" else (
        rf_fx_info["yield"] if rf_fx_info else rf_us_fetched)
    if o_rf:
        rf_us = st.number_input("U.S. 10Y yield (%)", -2.0, 60.0, round(rf_us_fetched * 100, 3), 0.01,
                                format="%.3f", key="rf_us") / 100
        rf_fx = st.number_input(f"{cname} 10Y yield (%)", -2.0, 60.0, round(rf_fx_fetched * 100, 3), 0.01,
                                format="%.3f", key=f"rf_fx_{ckey}") / 100
    else:
        rf_us, rf_fx = rf_us_fetched, rf_fx_fetched

    tax = tax_us if use_us_tax else tax_fx

    # Foreign currency spread: auto unless overridden
    auto_preview = compute(beta_u, tax, debt_share, erp, rf_us, rf_fx, spread_us, None, method)
    o_fx_spread = st.toggle(f"{ccy} BBB spread", key="t_fx_spread",
                            help="Off = auto calculated from the USD cost of debt")
    if o_fx_spread:
        spread_fx_override = st.number_input(f"{ccy} BBB spread (%)", -5.0, 30.0,
                                             round(auto_preview["spread_fx_auto"] * 100, 3), 0.01,
                                             format="%.3f", key=f"spread_fx_{ckey}") / 100
    else:
        spread_fx_override = None
        st.caption(f"Auto {ccy} spread: **{pct(auto_preview['spread_fx_auto'], 3)}**")

res = compute(beta_u, tax, debt_share, erp, rf_us, rf_fx, spread_us, spread_fx_override, method)

# Status of each input
s_beta = "Manual" if o_beta else betas["status"]
s_tax = "Manual" if o_tax else taxes["status"]
s_spread = "Manual" if o_spread else bbb["status"]
s_rf = "Manual" if o_rf else ylds["status"]
s_fx_spread = "Manual" if o_fx_spread else "Calculated"

# --------------------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------------------
st.markdown(
    f"""
    <div class="hero">
      <h1>International Cost of Capital</h1>
      <p>Cost of equity, cost of debt and WACC for a <b>{industry}</b> project,
      in <b>USD</b> and in <b>{ccy}</b> ({cname}).</p>
    </div>
    """,
    unsafe_allow_html=True,
)

warnings = []
if tax_fx_fetched is None and not o_tax and ckey != "united states":
    warnings.append(f"OECD has no tax rate for {cname}; the U.S. rate is used. Override it in the sidebar if needed.")
if rf_fx_info is None and ckey != "united states" and not o_rf:
    warnings.append(f"No 10Y yield found for {cname}; the U.S. yield is used. Override it in the sidebar.")
for src, d in [("Betas", betas), ("BBB spread", bbb), ("Tax rates", taxes), ("Bond yields", ylds)]:
    if d["status"] == "Fallback":
        warnings.append(f"{src}: live source unreachable, using an offline snapshot ({d['as_of']}). "
                        "Click **Refresh data** or override manually.")
for w in warnings:
    st.warning(w, icon="⚠️")

st.markdown(
    f"""
    <div class="chip-row">
      <span class="chip">Unlevered β <b>{beta_u:.2f}</b>{badge(s_beta)}</span>
      <span class="chip">Levered β <b>{res['beta_l']:.2f}</b>{badge('Calculated')}</span>
      <span class="chip">Tax rate ({'U.S.' if use_us_tax else cname}) <b>{pct(tax)}</b>{badge(s_tax)}</span>
      <span class="chip">ERP <b>{pct(erp)}</b></span>
      <span class="chip">Debt share <b>{pct(debt_share, 0)}</b></span>
      <span class="chip">USD BBB spread <b>{pct(spread_us)}</b>{badge(s_spread)}</span>
      <span class="chip">U.S. 10Y <b>{pct(rf_us, 3)}</b>{badge(s_rf)}</span>
      <span class="chip">{cname} 10Y <b>{pct(rf_fx, 3)}</b>{badge(s_rf)}</span>
    </div>
    """,
    unsafe_allow_html=True,
)

tab_res, tab_all, tab_assume, tab_src, tab_ver = st.tabs(
    ["📊 Results", "🏭 All industries", "📘 Assumptions & formulas", "🗂️ Data sources", "✅ Verification"])

# --------------------------------------------------------------------------------------
# Results tab
# --------------------------------------------------------------------------------------
with tab_res:
    c1, c2 = st.columns(2, gap="large")

    def card(col, title, sub, cls, rf, spread, spread_status, ke, kd, kd_at, wacc, big_cls):
        col.markdown(
            f"""
            <div class="card {cls}">
              <h3>{title}</h3>
              <div class="sub">{sub}</div>
              <div class="mrow"><span class="lbl">Risk free rate (10Y govt.)</span><span class="val">{pct(rf, 3)}</span></div>
              <div class="mrow"><span class="lbl">BBB spread {badge(spread_status)}</span><span class="val">{pct(spread, 3)}</span></div>
              <div class="mrow"><span class="lbl">Cost of equity</span><span class="val">{pct(ke)}</span></div>
              <div class="mrow"><span class="lbl">Pre-tax cost of debt</span><span class="val">{pct(kd)}</span></div>
              <div class="mrow"><span class="lbl">After-tax cost of debt</span><span class="val">{pct(kd_at)}</span></div>
              <div class="mrow big {big_cls}"><span class="lbl">WACC</span><span class="val">{pct(wacc)}</span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    card(c1, "🇺🇸 USD", "United States dollar", "card-usd", res["rf_us"], res["spread_us"], s_spread,
         res["ke_us"], res["kd_us"], res["kd_us_at"], res["wacc_us"], "usd")
    card(c2, f"🌐 {ccy}", f"{cname} currency", "card-fx", res["rf_fx"], res["spread_fx"], s_fx_spread,
         res["ke_fx"], res["kd_fx"], res["kd_fx_at"], res["wacc_fx"], "fx")

    st.write("")
    summary = pd.DataFrame({
        "Metric": ["Risk free rate", "BBB spread", "Levered beta", "Cost of equity",
                   "Pre-tax cost of debt", "After-tax cost of debt", "WACC"],
        "USD": [res["rf_us"], res["spread_us"], res["beta_l"], res["ke_us"], res["kd_us"], res["kd_us_at"], res["wacc_us"]],
        fx_lbl: [res["rf_fx"], res["spread_fx"], res["beta_l"], res["ke_fx"], res["kd_fx"], res["kd_fx_at"], res["wacc_fx"]],
    })
    summary["Difference (pp)"] = (summary[fx_lbl] - summary["USD"]) * 100

    left, right = st.columns([1.05, 1], gap="large")
    with left:
        st.markdown("##### Side by side")
        show = summary.copy()
        for c in ["USD", fx_lbl]:
            show[c] = [f"{v:.2f}" if m == "Levered beta" else pct(v, 3) for m, v in zip(show["Metric"], show[c])]
        show["Difference (pp)"] = [("" if m == "Levered beta" else f"{d:+.3f}")
                                   for m, d in zip(show["Metric"], show["Difference (pp)"])]
        st.dataframe(show, hide_index=True, width="stretch")
    with right:
        st.markdown("##### Visual comparison")
        chart_df = summary[summary["Metric"].isin(["Cost of equity", "After-tax cost of debt", "WACC"])].melt(
            id_vars="Metric", value_vars=["USD", fx_lbl], var_name="Currency", value_name="Rate")
        chart_df["Currency"] = chart_df["Currency"].where(chart_df["Currency"] == "USD", f"{fx_lbl} ")
        chart = alt.Chart(chart_df).mark_bar(cornerRadiusTopLeft=5, cornerRadiusTopRight=5).encode(
            x=alt.X("Metric:N", sort=["Cost of equity", "After-tax cost of debt", "WACC"], title=None,
                    axis=alt.Axis(labelAngle=0)),
            xOffset="Currency:N",
            y=alt.Y("Rate:Q", axis=alt.Axis(format="%"), title=None),
            color=alt.Color("Currency:N", scale=alt.Scale(range=[USD_COLOR, FX_COLOR]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=["Metric", "Currency", alt.Tooltip("Rate:Q", format=".2%")],
        ).properties(height=290)
        st.altair_chart(chart, width="stretch")

    st.info(
        f"**Reading the results.** The {ccy} WACC is {pct(res['wacc_fx'])} versus {pct(res['wacc_us'])} in USD. "
        f"The gap comes from the difference in 10-year government yields ({pct(rf_fx, 2)} vs {pct(rf_us, 2)}), "
        "which proxies for expected inflation differences between the two currencies. Business risk "
        "(beta), the ERP, the debt share and the tax rate are identical in both columns.",
        icon="💡",
    )

# --------------------------------------------------------------------------------------
# All industries
# --------------------------------------------------------------------------------------
def all_industries_table() -> pd.DataFrame:
    rows = []
    for _, r in beta_df.iterrows():
        bu = beta_u if (o_beta and r["Industry"] == industry) else float(r["Unlevered beta"])
        x = compute(bu, tax, debt_share, erp, rf_us, rf_fx, spread_us, spread_fx_override, method)
        rows.append({
            "Industry": r["Industry"], "Unlevered beta": bu, "Levered beta": x["beta_l"],
            "Cost of equity (USD)": x["ke_us"], f"Cost of equity ({fx_lbl})": x["ke_fx"],
            "After-tax cost of debt (USD)": x["kd_us_at"], f"After-tax cost of debt ({fx_lbl})": x["kd_fx_at"],
            "WACC (USD)": x["wacc_us"], f"WACC ({fx_lbl})": x["wacc_fx"],
        })
    return pd.DataFrame(rows)


all_df = all_industries_table()

with tab_all:
    st.markdown(f"##### Every industry under the same assumptions ({cname}, tax {pct(tax)}, "
                f"ERP {pct(erp)}, debt {pct(debt_share, 0)})")
    a1, a2, a3 = st.columns([1.2, 1, 1])
    metric = a1.selectbox("Rank by", ["WACC", "Cost of equity", "Unlevered beta"], index=0)
    search = a2.text_input("Filter industries", placeholder="e.g. software, retail")
    top_n = a3.slider("Industries in chart", 10, max(10, len(all_df)), min(30, len(all_df)))

    view = all_df.copy()
    if search:
        view = view[view["Industry"].str.contains(search, case=False, regex=False)]
    sort_col = "Unlevered beta" if metric == "Unlevered beta" else f"{metric} (USD)"
    view = view.sort_values(sort_col, ascending=False)

    chart_rows = view.head(top_n)
    if metric == "Unlevered beta":
        cd = chart_rows[["Industry", "Unlevered beta"]].rename(columns={"Unlevered beta": "Value"})
        cd["Series"] = "Unlevered beta"
        fmt = ".2f"
    else:
        cd = chart_rows.melt(id_vars="Industry", value_vars=[f"{metric} (USD)", f"{metric} ({fx_lbl})"],
                             var_name="Series", value_name="Value")
        fmt = ".2%"
    cd["Selected"] = cd["Industry"] == industry
    bars = alt.Chart(cd).mark_bar(cornerRadiusEnd=4).encode(
        y=alt.Y("Industry:N", sort=chart_rows["Industry"].tolist(), title=None),
        x=alt.X("Value:Q", title=None, axis=alt.Axis(format=fmt.replace(".2", ""))),
        yOffset="Series:N",
        color=alt.Color("Series:N", scale=alt.Scale(range=[USD_COLOR, FX_COLOR]),
                        legend=alt.Legend(orient="top", title=None)),
        opacity=alt.condition("datum.Selected", alt.value(1.0), alt.value(0.72)),
        tooltip=["Industry", "Series", alt.Tooltip("Value:Q", format=fmt)],
    ).properties(height=max(260, 30 * len(chart_rows)))
    st.altair_chart(bars, width="stretch")
    st.caption(f"Selected industry: **{industry}** (shown at full opacity).")

    pct_cols = [c for c in view.columns if c not in ("Industry", "Unlevered beta", "Levered beta")]
    st.dataframe(
        view.style.format({**{c: "{:.2%}" for c in pct_cols}, "Unlevered beta": "{:.2f}", "Levered beta": "{:.2f}"})
            .apply(lambda r: ["background-color: #E8F4F3" if r["Industry"] == industry else "" for _ in r], axis=1),
        hide_index=True, width="stretch", height=420,
    )

# --------------------------------------------------------------------------------------
# Assumptions & formulas
# --------------------------------------------------------------------------------------
with tab_assume:
    st.markdown("##### Main assumptions")
    st.markdown(
        f"""
- **Same project, two currencies.** The project's business risk (unlevered beta), equity risk premium,
  target debt share and tax rate are the same in both columns. Only the currency changes.
- **Risk free rates** are the 10-year government bond yields of the U.S. and of {cname}.
  Their difference captures expected inflation (currency) differences.
- **Unlevered beta** is Damodaran's US industry unlevered beta, relevered with the Hamada formula
  at the target debt share.
- **Cost of debt** assumes the project borrows at a **BBB (Baa2)** rating, using Damodaran's spread for
  large non-financial service firms.
- **Tax rate**: one rate (currently **{'U.S.' if use_us_tax else cname}**, {pct(tax)}) is used for both the
  relevered beta and the debt tax shield in both currencies. Rates are OECD combined (central plus
  sub-central) statutory corporate income tax rates.
- **Currency conversion**: {'interest rate parity (multiplicative)' if method == 'parity' else 'additive yield differential'}.
  The foreign BBB spread is auto calculated unless you override it.
        """
    )
    st.markdown("##### Formulas")
    st.latex(r"D/E = \frac{w_D}{1-w_D} \qquad \beta_L = \beta_U\left[1+(1-t)\frac{D}{E}\right]")
    st.latex(r"k_E^{\$} = r_f^{\$} + \beta_L \times ERP \qquad k_D^{\$} = r_f^{\$} + s_{BBB}^{\$}")
    if method == "parity":
        st.latex(r"k_E^{FC} = (1+k_E^{\$})\frac{1+r_f^{FC}}{1+r_f^{\$}} - 1 \qquad "
                 r"k_D^{FC} = (1+k_D^{\$})\frac{1+r_f^{FC}}{1+r_f^{\$}} - 1")
        st.latex(r"s_{BBB}^{FC,\ auto} = k_D^{FC} - r_f^{FC} = s_{BBB}^{\$}\,\frac{1+r_f^{FC}}{1+r_f^{\$}}")
    else:
        st.latex(r"k_E^{FC} = r_f^{FC} + \beta_L \times ERP \qquad k_D^{FC} = r_f^{FC} + s_{BBB}^{\$}")
    st.latex(r"\text{Override: } k_D^{FC} = r_f^{FC} + s_{BBB}^{FC,\ manual}")
    st.latex(r"WACC = (1-w_D)\,k_E + w_D\,k_D\,(1-t)")
    st.caption("FC = foreign currency. All rates are annual nominal rates in the stated currency.")

# --------------------------------------------------------------------------------------
# Data sources
# --------------------------------------------------------------------------------------
with tab_src:
    st.markdown("##### Where the numbers come from")
    rf_fx_date = (rf_fx_info or {}).get("date", "n/a") if ckey != "united states" else (rf_us_info or {}).get("date", "n/a")
    src = pd.DataFrame([
        {"Input": "Unlevered industry betas (US)", "Provider": "Aswath Damodaran, NYU Stern",
         "Status": betas["status"], "Data as of": betas["as_of"], "Link": BETAS_URL},
        {"Input": "BBB (Baa2) USD spread, large non-financial firms", "Provider": "Aswath Damodaran, NYU Stern",
         "Status": bbb["status"], "Data as of": bbb["as_of"], "Link": RATINGS_URL},
        {"Input": "Corporate income tax rates (combined statutory)", "Provider": "OECD Corporate Tax Statistics",
         "Status": taxes["status"], "Data as of": taxes["as_of"], "Link": OECD_PAGE_URL},
        {"Input": "U.S. 10Y government bond yield", "Provider": "Trading Economics",
         "Status": ylds["status"], "Data as of": f"{(rf_us_info or {}).get('date', 'n/a')} · {ylds['as_of']}",
         "Link": TE_BONDS_URL},
        {"Input": f"{cname} 10Y government bond yield", "Provider": "Trading Economics",
         "Status": ylds["status"] if rf_fx_info or ckey == "united states" else "Missing",
         "Data as of": f"{rf_fx_date} · {ylds['as_of']}", "Link": TE_BONDS_URL},
    ])
    st.dataframe(src, hide_index=True, width="stretch",
                 column_config={"Link": st.column_config.LinkColumn("Link", display_text="Open source")})
    st.caption(
        f"Tax rates are pulled from the OECD Corporate Tax Statistics database (SDMX API: {OECD_API_URL}), "
        "the same data behind the OECD report chapter linked above. All sources are cached for up to "
        "12 hours; use **Refresh data** in the sidebar to fetch them again."
    )
    with st.expander("Fetch diagnostics"):
        for name, d in [("Betas", betas), ("BBB spread", bbb), ("Tax rates", taxes), ("Bond yields", ylds)]:
            st.write(f"**{name}**: {d['status']} · fetched {d['fetched']}" + (f" · error: `{d['error']}`" if d["error"] else ""))
    with st.expander("All fetched 10Y yields and tax rates"):
        yt = pd.DataFrame([
            {"Country": display_name(k), "10Y yield": v["yield"], "Yield date": v["date"],
             "Tax rate": tax_rates.get(k), "Tax year": taxes["years"].get(k)}
            for k, v in sorted(yield_data.items(), key=lambda kv: display_name(kv[0]))
        ])
        st.dataframe(yt.style.format({"10Y yield": "{:.3%}", "Tax rate": "{:.2%}", "Tax year": "{:.0f}"}, na_rep="n/a"),
                     hide_index=True, width="stretch")

# --------------------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------------------
with tab_ver:
    st.markdown("##### Built-in sanity checks (run live on your current inputs)")
    tol = 1e-12

    eq = compute(beta_u, tax, debt_share, erp, rf_us, rf_us, spread_us, None, method)
    chk1 = all(abs(eq[a] - eq[b]) < tol for a, b in [("ke_us", "ke_fx"), ("kd_us", "kd_fx"), ("wacc_us", "wacc_fx")])

    z = compute(beta_u, tax, 0.0, erp, rf_us, rf_fx, spread_us, spread_fx_override, method)
    chk2 = abs(z["wacc_us"] - z["ke_us"]) < tol and abs(z["wacc_fx"] - z["ke_fx"]) < tol

    base = compute(beta_u, tax, debt_share, erp, rf_us, rf_fx, spread_us, None, method)
    ovr = compute(beta_u, tax, debt_share, erp, rf_us, rf_fx, spread_us, base["spread_fx_auto"] + 0.02, method)
    chk3 = all(abs(base[k] - ovr[k]) < tol for k in ["ke_us", "kd_us", "wacc_us"])

    checks = pd.DataFrame([
        {"Check": "1. Equal government yields give equal results (foreign spread override off)",
         "Result": "✅ Pass" if chk1 else "❌ Fail",
         "Evidence": f"WACC USD {pct(eq['wacc_us'], 4)} vs FC {pct(eq['wacc_fx'], 4)}"},
        {"Check": "2. Zero debt makes WACC equal the cost of equity",
         "Result": "✅ Pass" if chk2 else "❌ Fail",
         "Evidence": f"USD: WACC {pct(z['wacc_us'], 4)} = ke {pct(z['ke_us'], 4)}; "
                     f"FC: WACC {pct(z['wacc_fx'], 4)} = ke {pct(z['ke_fx'], 4)}"},
        {"Check": "3. Overriding the foreign spread leaves USD results unchanged",
         "Result": "✅ Pass" if chk3 else "❌ Fail",
         "Evidence": f"USD WACC {pct(base['wacc_us'], 4)} (auto) vs {pct(ovr['wacc_us'], 4)} (spread +2pp); "
                     f"FC WACC moves {pct(base['wacc_fx'], 4)} to {pct(ovr['wacc_fx'], 4)}"},
    ])
    st.dataframe(checks, hide_index=True, width="stretch")

    st.markdown("##### Step by step calculation with your current inputs")
    st.caption("Use this to reproduce the numbers in a spreadsheet or calculator.")
    ratio_txt = f"(1 + {rf_fx:.6f}) / (1 + {rf_us:.6f}) = {(1 + rf_fx) / (1 + rf_us):.6f}"
    steps = f"""
| Step | Formula | Value |
|---|---|---|
| D/E | {debt_share:.4f} / (1 − {debt_share:.4f}) | {res['de']:.6f} |
| Levered beta | {beta_u:.4f} × [1 + (1 − {tax:.4f}) × {res['de']:.6f}] | {res['beta_l']:.6f} |
| USD cost of equity | {rf_us:.6f} + {res['beta_l']:.6f} × {erp:.4f} | {res['ke_us']:.6f} |
| USD pre-tax cost of debt | {rf_us:.6f} + {spread_us:.6f} | {res['kd_us']:.6f} |
| USD WACC | {1 - debt_share:.4f} × {res['ke_us']:.6f} + {debt_share:.4f} × {res['kd_us']:.6f} × (1 − {tax:.4f}) | {res['wacc_us']:.6f} |
| Currency factor | {ratio_txt if method == 'parity' else 'additive method: swap rf'} | |
| {ccy} cost of equity | {'(1 + ke$) × factor − 1' if method == 'parity' else 'rf_fx + βL × ERP'} | {res['ke_fx']:.6f} |
| {ccy} pre-tax cost of debt | {'rf_fx + manual spread' if spread_fx_override is not None else ('(1 + kd$) × factor − 1' if method == 'parity' else 'rf_fx + USD spread')} | {res['kd_fx']:.6f} |
| {ccy} WACC | {1 - debt_share:.4f} × {res['ke_fx']:.6f} + {debt_share:.4f} × {res['kd_fx']:.6f} × (1 − {tax:.4f}) | {res['wacc_fx']:.6f} |
"""
    st.markdown(steps)

# --------------------------------------------------------------------------------------
# Downloads
# --------------------------------------------------------------------------------------
st.divider()
st.markdown("##### ⬇️ Download results")

inputs_df = pd.DataFrame([
    ("Industry", industry, s_beta), ("Target country", cname, ""), ("Foreign currency", ccy, ""),
    ("Unlevered beta", beta_u, s_beta), ("Tax rate used", tax, f"{'U.S.' if use_us_tax else cname} · {s_tax}"),
    ("U.S. tax rate", tax_us, s_tax), (f"{cname} tax rate", tax_fx, s_tax),
    ("Equity risk premium", erp, "User"), ("Debt share D/(D+E)", debt_share, "User"),
    ("USD BBB spread", spread_us, s_spread), (f"{ccy} BBB spread", res["spread_fx"], s_fx_spread),
    ("U.S. 10Y yield", rf_us, s_rf), (f"{cname} 10Y yield", rf_fx, s_rf),
    ("Conversion method", method_lbl, ""), ("Generated", now_utc(), ""),
], columns=["Input", "Value", "Source / status"])

d1, d2, d3 = st.columns(3)
d1.download_button("Results summary (CSV)", summary.to_csv(index=False).encode(),
                   file_name=f"cost_of_capital_{ccy}_{industry[:20]}.csv".replace(" ", "_").replace("/", "-"),
                   mime="text/csv", width="stretch")
d2.download_button("All industries (CSV)", all_df.to_csv(index=False).encode(),
                   file_name=f"all_industries_{ccy}.csv", mime="text/csv", width="stretch")

xbuf = io.BytesIO()
with pd.ExcelWriter(xbuf, engine="openpyxl") as xw:
    inputs_df.astype({"Value": str}).to_excel(xw, sheet_name="Inputs", index=False)
    summary.to_excel(xw, sheet_name="Results", index=False)
    all_df.to_excel(xw, sheet_name="All industries", index=False)
d3.download_button("Full workbook (Excel)", xbuf.getvalue(), file_name=f"cost_of_capital_{ccy}.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   width="stretch")

st.markdown(
    '<p class="note">Educational tool. Data © their respective providers (Damodaran Online, OECD, '
    "Trading Economics). Always verify calculations independently.</p>",
    unsafe_allow_html=True,
)
