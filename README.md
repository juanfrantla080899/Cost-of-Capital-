# 🌎 International Cost of Capital App

A Streamlit app that compares a project's **cost of equity, cost of debt and WACC in USD** and in the
**currency of any selected country**, using live market and tax data.

**Live app:** `https://<your-app-name>.streamlit.app` (replace after deploying)

---

## What the app does

| Feature | Details |
|---|---|
| Live data | Industry betas and BBB spread (Damodaran), corporate tax rates (OECD), 10Y government yields (Trading Economics) |
| Sources and dates | Every input shows its provider, data date and status badge (Live, Fallback, Manual, Calculated) |
| Refresh | 🔄 **Refresh data** button clears the cache and fetches every source again |
| Project setup | Industry, target country, equity risk premium, target debt share |
| Tax switch | One click to use the U.S. or the selected country's tax rate for both currencies |
| Manual overrides | Unlevered beta, both tax rates, USD BBB spread, both 10Y yields, and the foreign currency BBB spread (shows the auto value when switched off) |
| Results | USD and foreign currency side by side, with a comparison table and chart |
| All industries | Every Damodaran industry under the same assumptions, ranked and charted |
| Assumptions | Plain language assumptions plus all formulas |
| Verification | Three live sanity checks and a step by step calculation you can reproduce in Excel |
| Downloads | Results CSV, all industries CSV, and a full Excel workbook (inputs, results, all industries) |

**Defaults on launch:** Software (System & Application), United States as the selected country,
U.S. tax rate, 6.5% equity risk premium, 30% debt financing.

---

## Data sources

| Input | Source | How it is fetched |
|---|---|---|
| Unlevered beta (US industries) | [Damodaran, betas.xls](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls) | Downloads the Excel file and reads the **Unlevered beta** column of the Industry Averages sheet; the "Date updated" stamp is shown |
| BBB USD spread | [Damodaran, ratings.html](https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html) | Reads the **Baa2/BBB** row of the table for large non-financial service firms |
| Corporate tax rates | [OECD Corporate Tax Statistics](https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html#chapter-d1e2342-08e74a33fb) | The report chapter is a chart, so the app queries the same OECD database through the official SDMX API (`OECD.CTP.TPS, DSD_TAX_CIT@DF_CIT`) and takes the **combined (central plus sub-central) statutory rate** for the latest year |
| 10Y government yields | [Trading Economics, bonds](https://tradingeconomics.com/bonds) | Scrapes the public bonds table (no API key). If a country is missing it tries that country's own bond page |

Data is cached for up to 12 hours. If a source cannot be reached, the app keeps working with a clearly
labeled offline snapshot and shows a warning, and every value can be overwritten manually.

---

## Methodology

All rates are annual and nominal. FC means foreign currency.

**1. Relever the industry beta at the target capital structure (Hamada)**

```
D/E = w_D / (1 − w_D)
βL  = βU × [1 + (1 − t) × D/E]
```

**2. USD costs**

```
Cost of equity (USD)      ke$ = rf$ + βL × ERP
Pre-tax cost of debt (USD) kd$ = rf$ + BBB spread$
```

**3. Foreign currency costs (default: interest rate parity, multiplicative)**

```
Currency factor  F = (1 + rf_FC) / (1 + rf$)
ke_FC = (1 + ke$) × F − 1
kd_FC = (1 + kd$) × F − 1          (auto)
Auto FC spread = kd_FC − rf_FC = spread$ × F
Override:  kd_FC = rf_FC + manual FC spread
```

An **Advanced** option switches to an additive yield differential
(`ke_FC = rf_FC + βL × ERP`, `kd_FC = rf_FC + spread$`) in case the class formulas use that version.

**4. WACC (same tax rate, debt share and ERP in both currencies)**

```
WACC = (1 − w_D) × ke + w_D × kd × (1 − t)
```

### Main assumptions

* The project's business risk (unlevered beta), ERP, target debt share and tax rate are identical in both currencies; only the currency changes.
* The gap between the 10Y government yields reflects expected inflation differences between the currencies.
* The project borrows at a BBB (Baa2) rating.
* Tax rates are OECD combined statutory corporate income tax rates.

---

## Verification

The **✅ Verification** tab runs these checks live on whatever inputs are selected:

1. **Equal government yields give equal results** (foreign spread override off): with rf_FC = rf$, the factor F = 1, so every foreign currency result equals its USD counterpart.
2. **Zero debt makes WACC equal the cost of equity**: with w_D = 0, WACC = ke in both currencies.
3. **Overriding the foreign currency spread leaves USD results unchanged**: the override only enters kd_FC.

**Hand check of the default case** (fill in the live values shown in the app):

```
βU = 1.23, t = 25.6%, w_D = 30%, ERP = 6.5%, rf$ = 4.10%, spread$ = 1.11%
D/E  = 0.30 / 0.70 = 0.428571
βL   = 1.23 × [1 + 0.744 × 0.428571] = 1.6222
ke$  = 4.10% + 1.6222 × 6.5% = 14.644%
kd$  = 4.10% + 1.11% = 5.21%   → after tax 5.21% × 0.744 = 3.876%
WACC = 0.70 × 14.644% + 0.30 × 3.876% = 11.414%
```

---

## Run locally

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Push `app.py`, `requirements.txt`, `README.md` and the `.streamlit/` folder to a public GitHub repository.
2. Go to [share.streamlit.io](https://share.streamlit.io), click **Create app**, pick the repository, branch `main` and file `app.py`.
3. Click **Deploy**. Paste the app URL at the top of this README.

## Repository structure

```
├── app.py                 # Streamlit app
├── requirements.txt       # Python dependencies
├── README.md              # This file
└── .streamlit/
    └── config.toml        # Theme (colors, light mode)
```

---

*Built with the help of AI (Claude). All calculations were checked independently, as described in the Verification section.
Data belongs to Aswath Damodaran (NYU Stern), the OECD and Trading Economics.*
