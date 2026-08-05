# Attribution — integration test fixtures

Human-readable attribution for the 26 real-world DOCX/XLSX documents checked
into `tests/integration/fixtures/` (`docx/`, `xlsx/`). `manifest.yaml` in
that directory remains the source of truth (sha256, add date, per-document
notes) — this file exists because visible attribution is itself part of
several of these licenses' terms, not a duplicate record.

One 27th fixture, `iot-report-2022-national-strategies-excerpt.docx`, stays
gitignored and out of this file — authorship risk (not a license grant),
explicitly accepted by the repo owner 2026-08-04, see its `manifest.yaml`
entry.

## CC BY 4.0 (16 documents)

Attribution required, no additional restriction. Full license:
https://creativecommons.org/licenses/by/4.0/

- IPBES (2023). *Summary for Policymakers of the Thematic Assessment of
  Invasive Alien Species and their Control.* IPBES Secretariat, Bonn,
  Germany. — https://zenodo.org/records/11254974
- hackAIR Consortium. *D7.7 Final Pilot Evaluation Report v1.0.* Horizon
  2020 project 689443. — https://zenodo.org/records/2531140
- MARCO-BOLO Project. *3rd Co-Design/Co-Creation Workshop Report.* Horizon
  Europe grant 101082021. — https://zenodo.org/records/17244832
- European Food Safety Authority. *User Guide – Dashboard on Trichinella.*
  Supplement to DOI 10.2903/j.efsa.2024.9106 (EU Zoonoses Report). —
  https://zenodo.org/records/13987050
- European Food Safety Authority. *User Guide – Dashboard on
  Echinococcus.* Supplement to the EU Zoonoses Report. —
  https://zenodo.org/records/13987056
- European Food Safety Authority. *User Guide – Dashboard on Rabies.*
  Supplement to the EU Zoonoses Report. —
  https://zenodo.org/records/13987080
- UKRI. *User Behaviour Survey Final Report: The motivators/enablers, and
  barriers to sustainable Digital Research Infrastructure use.* —
  https://zenodo.org/records/7827919
- One Health EJP. *D3.20 Report on evaluation of finalized JRPs (second
  round), May 2023.* — https://zenodo.org/records/8091310
- KTH dESA. *Least-cost technology analysis for electricity access in
  Ecuador (OnSSET modeling report).* —
  https://zenodo.org/records/10601101
- World Bank Group. *GovTech Maturity Index (GTMI) 2025 Update.* DOI
  10.60572/50tk-0s28. —
  https://datacatalog.worldbank.org/search/dataset/0037889
- DAISY Project (Horizon Europe grant 101181857). *Annex C: Simplified
  TRD2 scoring sheet, including explanatory notes and radar chart output
  for TRD2 applications.* — https://zenodo.org/records/17160272
- Acorn and Olives / RADIANT project. *RADIANT-Metrics: state-of-the-art
  quantitative framework and decision support tool.* —
  https://zenodo.org/records/15553016
- Eurostat. *Electricity production, consumption and market overview*,
  Statistics Explained. —
  https://ec.europa.eu/eurostat/statistics-explained/images/2/2b/Electricity_production_consumption_market_2023.xlsx
- FOODRUS Project (Horizon 2020 grant 101000617). *FOODRUS Dashboard —
  IoT in the Food Supply Chain.* — https://zenodo.org/records/8119271
- Eurostat. *Waste statistics*, Statistics Explained. —
  https://ec.europa.eu/eurostat/statistics-explained/images/1/1f/Waste_statistics_30_09_2024cor.xlsx
- Eurostat. *Renewable energy statistics*, Statistics Explained. —
  https://ec.europa.eu/eurostat/statistics-explained/images/5/5b/Renewable_energy-tables_and_figures_2025_prov.xlsx

## CC BY 3.0 IGO (2 documents)

Attribution required, no additional restriction. Full license:
https://creativecommons.org/licenses/by/3.0/igo/

- World Bank. *Global Economic Prospects, January 2022*, Chapter 4 Annex.
  Washington, DC: World Bank. —
  https://thedocs.worldbank.org/en/doc/cb15f6d7442eadedf75bb95c4fdec1b3-0350012022/related/GEP-January-2022-Chapter4-Annex.xlsx
- World Bank. *Global Economic Prospects, January 2022*, Chapter 3 Annex.
  Washington, DC: World Bank. —
  https://thedocs.worldbank.org/en/doc/cb15f6d7442eadedf75bb95c4fdec1b3-0350012022/related/GEP-January-2022-Chapter3-Annex.xlsx

## EU reuse right — Commission Decision 2011/833/EU (5 documents)

European Commission documents, reusable under the Commission's standard
reuse decision (source attribution required). Full text:
https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32011D0833

- European Commission. SWD(2018) 254 final — *Impact Assessment
  accompanying the proposal on reducing marine litter (single-use
  plastics and fishing gear).* EUR-Lex CELEX 52018SC0254. Main body. —
  http://publications.europa.eu/resource/cellar/4d0542a2-6256-11e8-ab9c-01aa75ed71a1.0001.01/DOC_1
- European Commission. SWD(2018) 254 final, Annex. EUR-Lex CELEX
  52018SC0254. —
  http://publications.europa.eu/resource/cellar/4d0542a2-6256-11e8-ab9c-01aa75ed71a1.0001.01/DOC_2
- European Commission. SWD(2021) 396 final — *Impact Assessment
  accompanying the proposal on improving working conditions in platform
  work.* EUR-Lex CELEX 52021SC0396. —
  http://publications.europa.eu/resource/cellar/48491c8f-59bb-11ec-91ac-01aa75ed71a1.0001.01/DOC_1
- European Commission. SWD(2020) 335 final — *Impact Assessment on
  batteries and waste batteries.* EUR-Lex CELEX 52020SC0335. Part 2. —
  http://publications.europa.eu/resource/cellar/5ee7d299-3ad8-11eb-b27b-01aa75ed71a1.0001.01/DOC_2
- European Commission. SWD(2020) 335 final, Part 3 (Annex). EUR-Lex CELEX
  52020SC0335. —
  http://publications.europa.eu/resource/cellar/5ee7d299-3ad8-11eb-b27b-01aa75ed71a1.0001.01/DOC_3

## US public domain — US federal government work (3 documents)

No copyright under 17 U.S.C. § 105; no attribution legally required, listed
here anyway for provenance transparency.

- U.S. Energy Information Administration. *Short-Term Energy Outlook —
  Chart Gallery.* — https://www.eia.gov/outlooks/steo/xls/chart-gallery.xlsx
  (source always serves the current month's edition — the checksum
  recorded in `manifest.yaml` will legitimately drift on re-download)
- U.S. Energy Information Administration. *Annual Energy Outlook 2026 —
  Narrative Figures.* —
  https://www.eia.gov/outlooks/aeo/excel/Narrative_Figures.xlsx
- U.S. Energy Information Administration. *International Energy Outlook
  2023 — Narrative Figures.* —
  https://www.eia.gov/outlooks/ieo/excel/IEO2023_Narrative_AllFigures.xlsx
