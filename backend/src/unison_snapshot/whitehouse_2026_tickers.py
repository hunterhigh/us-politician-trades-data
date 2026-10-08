"""Source-bound ticker enrichment for Trump's 2026 White House 278-T trades."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from .alpaca_market import (
    SIP_EXCHANGES, TICKER, _asset_registry, _recover_unique_asset_name_tickers,
)
from .codec import encode
from .whitehouse_278t import TRUMP_2026_PROFILES
from .oge_reports import (
    TRUMP_SEPT_2026_DOCUMENT_ID, TRUMP_SEPT_2026_SOURCE_URL,
)


SCHEMA = "whitehouse-trump-2026-278t-ticker-mapping/v1"
TRUMP_PERSON_ID = "oge:076544f8ba0638cf"
SEMANTIC_BASIS = "alpaca_source_bound_semantic_alias"
PRIOR_TRUMP_BASIS = "prior_trump_exact_asset_name"
SOURCE_DIRECTORY_BASIS = "official_source_row_security_directory"
ALLOWED_BASES = {"alpaca_unique_asset_name", "alpaca_unique_classless_asset_name",
                 SEMANTIC_BASIS, PRIOR_TRUMP_BASIS, SOURCE_DIRECTORY_BASIS}
REPORTS = {profile["document_id"]: profile for profile in TRUMP_2026_PROFILES}
REPORTS[TRUMP_SEPT_2026_DOCUMENT_ID] = {
    "source_url": TRUMP_SEPT_2026_SOURCE_URL,
    "report_date": "2026-09-08",
}
ANNUAL_MAPPING_EVIDENCE = (
    "https://github.com/hunterhigh/us-politician-trades-data/blob/"
    "10bd7a05b68ce52b9d8650a2a669639c4d46b314/"
    "whitehouse/annual/ticker-mapping-current.json")
NASDAQ_LISTED_URL = (
    "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt")
OTHER_LISTED_URL = (
    "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt")
SECURITY_DIRECTORY_CHECKED_ON = "2026-09-28"
SOURCE_DIRECTORY_AMBIGUITY_GUARD = (
    "fixed_official_row+single_security_directory_symbol+"
    "single_active_sip_asset+no_debt_or_concatenated_asset")

# The official Nasdaq Trader symbol directories identify the listed security;
# the live Alpaca asset master remains the production gate for active SIP scope.
SECURITY_DIRECTORY = {
    "ACN": ("Accenture plc Class A Ordinary Shares (Ireland)", OTHER_LISTED_URL),
    "AGX": ("Argan, Inc. Common Stock", OTHER_LISTED_URL),
    "ALL": ("Allstate Corporation (The) Common Stock", OTHER_LISTED_URL),
    "AMTM": ("Amentum Holdings, Inc. Common Stock", OTHER_LISTED_URL),
    "ANDE": ("The Andersons, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "AOSL": ("Alpha and Omega Semiconductor Limited - Common Shares", NASDAQ_LISTED_URL),
    "ARI": ("Apollo Commercial Real Estate Finance, Inc", OTHER_LISTED_URL),
    "ASO": ("Academy Sports and Outdoors, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "AUB": ("Atlantic Union Bankshares Corporation Common Stock", OTHER_LISTED_URL),
    "BGC": ("BGC Group, Inc. - Class A Common Stock", NASDAQ_LISTED_URL),
    "BHE": ("Benchmark Electronics, Inc. Common Stock", OTHER_LISTED_URL),
    "BSX": ("Boston Scientific Corporation Common Stock", OTHER_LISTED_URL),
    "CCI": ("Crown Castle Inc. Common Stock", OTHER_LISTED_URL),
    "CFG": ("Citizens Financial Group, Inc. Common Stock", OTHER_LISTED_URL),
    "CHWY": ("Chewy, Inc. Class A Common Stock", OTHER_LISTED_URL),
    "CMCSA": ("Comcast Corporation - Class A Common Stock", NASDAQ_LISTED_URL),
    "CME": ("CME Group Inc. - Class A Common Stock", NASDAQ_LISTED_URL),
    "CRDO": ("Credo Technology Group Holding Ltd - Ordinary Shares", NASDAQ_LISTED_URL),
    "CRK": ("Comstock Resources, Inc. Common Stock", OTHER_LISTED_URL),
    "CRVL": ("CorVel Corp. - Common Stock", NASDAQ_LISTED_URL),
    "DGX": ("Quest Diagnostics Incorporated Common Stock", OTHER_LISTED_URL),
    "DIS": ("Walt Disney Company (The) Common Stock", OTHER_LISTED_URL),
    "DORM": ("Dorman Products, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "ECG": ("Everus Construction Group, Inc. Common Stock", OTHER_LISTED_URL),
    "EFC": ("Ellington Financial Inc. Common Stock ", OTHER_LISTED_URL),
    "EMN": ("Eastman Chemical Company Common Stock", OTHER_LISTED_URL),
    "F": ("Ford Motor Company Common Stock", OTHER_LISTED_URL),
    "FOXA": ("Fox Corporation - Class A Common Stock", NASDAQ_LISTED_URL),
    "G": ("Genpact Limited Common Stock", OTHER_LISTED_URL),
    "GOGO": ("Gogo Inc. - Common Stock", NASDAQ_LISTED_URL),
    "HCSG": ("Healthcare Services Group, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "HTZ": ("Hertz Global Holdings, Inc - Common Stock", NASDAQ_LISTED_URL),
    "IDXX": ("IDEXX Laboratories, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "ISRG": ("Intuitive Surgical, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "LTC": ("LTC Properties, Inc. Common Stock", OTHER_LISTED_URL),
    "LYFT": ("Lyft, Inc. - Class A Common Stock", NASDAQ_LISTED_URL),
    "MATW": ("Matthews International Corporation - Class A Common Stock", NASDAQ_LISTED_URL),
    "MDT": ("Medtronic plc. Ordinary Shares", OTHER_LISTED_URL),
    "MSFT": ("Microsoft Corporation - Common Stock", NASDAQ_LISTED_URL),
    "MWA": ("MUELLER WATER PRODUCTS Common Stock", OTHER_LISTED_URL),
    "MXL": ("MaxLinear, Inc - Common Stock", NASDAQ_LISTED_URL),
    "NBHC": ("National Bank Holdings Corporation Common Stock", OTHER_LISTED_URL),
    "NFLX": ("Netflix, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "NTAP": ("NetApp, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "NVDA": ("NVIDIA Corporation - Common Stock", NASDAQ_LISTED_URL),
    "NWN": ("Northwest Natural Holding Company Common Stock", OTHER_LISTED_URL),
    "NXPI": ("NXP Semiconductors N.V. - Common Stock", NASDAQ_LISTED_URL),
    "OFG": ("OFG Bancorp Common Stock", OTHER_LISTED_URL),
    "OI": ("O-I Glass, Inc. Common Stock", OTHER_LISTED_URL),
    "OMCL": ("Omnicell, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "OWL": ("Blue Owl Capital Inc. Class A Common Stock", OTHER_LISTED_URL),
    "OXM": ("Oxford Industries, Inc. Common Stock", OTHER_LISTED_URL),
    "PATK": ("Patrick Industries, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "PI": ("Impinj, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "PLTR": ("Palantir Technologies Inc. - Class A Common Stock", NASDAQ_LISTED_URL),
    "PLUS": ("ePlus inc. - Common Stock", NASDAQ_LISTED_URL),
    "POWL": ("Powell Industries, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "PRGO": ("Perrigo Company plc Ordinary Shares", OTHER_LISTED_URL),
    "SAFE": ("Safehold Inc. New Common Stock ", OTHER_LISTED_URL),
    "SEI": ("Solaris Energy Infrastructure, Inc. Class A Common Stock", OTHER_LISTED_URL),
    "SHEN": ("Shenandoah Telecommunications Co - Common Stock", NASDAQ_LISTED_URL),
    "SKT": ("Tanger Inc. Common Stock", OTHER_LISTED_URL),
    "SLB": ("SLB Limited Common Shares", OTHER_LISTED_URL),
    "STZ": ("Constellation Brands, Inc. Common Stock", OTHER_LISTED_URL),
    "TEL": ("TE Connectivity plc Ordinary Shares", OTHER_LISTED_URL),
    "TER": ("Teradyne, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "TKO": ("TKO Group Holdings, Inc. Class A Common Stock", OTHER_LISTED_URL),
    "UA": ("Under Armour, Inc. Class C Common Stock", OTHER_LISTED_URL),
    "UNH": ("UnitedHealth Group Incorporated Common Stock (DE)", OTHER_LISTED_URL),
    "VMC": ("Vulcan Materials Company (Holding Company) Common Stock", OTHER_LISTED_URL),
    "VST": ("Vistra Corp. Common Stock", OTHER_LISTED_URL),
    "VZ": ("Verizon Communications Inc. Common Stock", OTHER_LISTED_URL),
    "WOR": ("Worthington Enterprises, Inc. Common Shares", OTHER_LISTED_URL),
    "WU": ("Western Union Company (The) Common Stock", OTHER_LISTED_URL),
    "ZTS": ("Zoetis Inc. Class A Common Stock", OTHER_LISTED_URL),
    # Round 3: current official symbol directory names, checked 2026-09-28.
    "ABM": ("ABM Industries Incorporated Common Stock", OTHER_LISTED_URL),
    "AEO": ("American Eagle Outfitters, Inc. Common Stock", OTHER_LISTED_URL),
    "ALKS": ("Alkermes plc - Ordinary Shares", NASDAQ_LISTED_URL),
    "AMT": ("American Tower Corporation (REIT) Common Stock", OTHER_LISTED_URL),
    "AMZN": ("Amazon.com, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "APAM": ("Artisan Partners Asset Management Inc. Class A Common Stock", OTHER_LISTED_URL),
    "APP": ("Applovin Corporation - Class A Common Stock", NASDAQ_LISTED_URL),
    "AWI": ("Armstrong World Industries Inc Common Stock", OTHER_LISTED_URL),
    "BFS": ("Saul Centers, Inc. Common Stock", OTHER_LISTED_URL),
    "BRC": ("Brady Corporation Common Stock", OTHER_LISTED_URL),
    "CBRE": ("CBRE Group Inc Common Stock Class A", OTHER_LISTED_URL),
    "CHEF": ("The Chefs' Warehouse, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "CMG": ("Chipotle Mexican Grill, Inc. Common Stock", OTHER_LISTED_URL),
    "CNMD": ("CONMED Corporation Common Stock", OTHER_LISTED_URL),
    "COST": ("Costco Wholesale Corporation - Common Stock", NASDAQ_LISTED_URL),
    "CRH": ("CRH PLC Ordinary Shares", OTHER_LISTED_URL),
    "CSCO": ("Cisco Systems, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "CWK": ("Cushman & Wakefield Ltd. Common Shares", OTHER_LISTED_URL),
    "DAVE": ("Dave Inc.  - Class A Common Stock", NASDAQ_LISTED_URL),
    "EL": ("Estee Lauder Companies, Inc. (The) Common Stock", OTHER_LISTED_URL),
    "EQT": ("EQT Corporation Common Stock", OTHER_LISTED_URL),
    "EZPW": ("EZCORP, Inc. - Class A Non-Voting Common Stock", NASDAQ_LISTED_URL),
    "FUL": ("H. B. Fuller Company Common Stock", OTHER_LISTED_URL),
    "GEHC": ("GE HealthCare Technologies Inc. - Common Stock", NASDAQ_LISTED_URL),
    "HUBS": ("HubSpot, Inc. Common Stock", OTHER_LISTED_URL),
    "IBP": ("Installed Building Products, Inc. Common Stock", OTHER_LISTED_URL),
    "ICHR": ("Ichor Holdings - Ordinary Shares", NASDAQ_LISTED_URL),
    "KAI": ("Kadant Inc Common Stock", OTHER_LISTED_URL),
    "KR": ("Kroger Company (The) Common Stock", OTHER_LISTED_URL),
    # Page-7 v6 promotions: identities are still gated by one active SIP asset.
    "ABT": ("Abbott Laboratories Common Stock", OTHER_LISTED_URL),
    "AVGO": ("Broadcom Inc. - Common Stock", NASDAQ_LISTED_URL),
    "BDX": ("Becton, Dickinson and Company Common Stock", OTHER_LISTED_URL),
    "DUK": ("Duke Energy Corporation (Holding Company) Common Stock", OTHER_LISTED_URL),
    "FAST": ("Fastenal Company - Common Stock", NASDAQ_LISTED_URL),
    "HD": ("Home Depot, Inc. (The) Common Stock", OTHER_LISTED_URL),
    "MO": ("Altria Group, Inc.", OTHER_LISTED_URL),
    "NKE": ("Nike, Inc. Common Stock", OTHER_LISTED_URL),
    "PFE": ("Pfizer, Inc. Common Stock", OTHER_LISTED_URL),
    "PG": ("Procter & Gamble Company (The) Common Stock", OTHER_LISTED_URL),
    "PM": ("Philip Morris International Inc Common Stock", OTHER_LISTED_URL),
    "SPGI": ("S&P Global Inc. Common Stock", OTHER_LISTED_URL),
    "TXN": ("Texas Instruments Incorporated - Common Stock", NASDAQ_LISTED_URL),
    "WSO": ("Watsco, Inc. Common Stock", OTHER_LISTED_URL),
    "XOM": ("ExxonMobil Holdings Corporation Common Stock", OTHER_LISTED_URL),
    "LAUR": ("Laureate Education, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "LHX": ("L3Harris Technologies, Inc. Common Stock", OTHER_LISTED_URL),
    "OSIS": ("OSI Systems, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "PTC": ("PTC Inc. - Common Stock", NASDAQ_LISTED_URL),
    "PTEN": ("Patterson-UTI Energy, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "RDNT": ("RadNet, Inc. - Common Stock", NASDAQ_LISTED_URL),
    "RXO": ("RXO, Inc. Common Stock", OTHER_LISTED_URL),
    "SAH": ("Sonic Automotive, Inc. Common Stock", OTHER_LISTED_URL),
    "STE": ("STERIS plc (Ireland) Ordinary Shares", OTHER_LISTED_URL),
    "TEM": ("Tempus AI, Inc. - Class A Common Stock", NASDAQ_LISTED_URL),
    "TJX": ("TJX Companies, Inc. (The) Common Stock", OTHER_LISTED_URL),
    "UBER": ("Uber Technologies, Inc. Common Stock", OTHER_LISTED_URL),
    "UNFI": ("United Natural Foods, Inc. Common Stock", OTHER_LISTED_URL),
    "V": ("Visa Inc.", OTHER_LISTED_URL),
    "VICI": ("VICI Properties Inc. Common Stock", OTHER_LISTED_URL),
    "VSNT": ("Versant Media Group, Inc. - Class A Common Stock", NASDAQ_LISTED_URL),
    "WEN": ("Wendy's Company (The) - Common Stock", NASDAQ_LISTED_URL),
}

# Fixed extraction IDs bind each correction to one official PDF row.  Do not
# generalize these OCR spellings to other filings or split concatenated assets.
SEPTEMBER_SOURCE_ALIASES = {
    "oge-278t:3964baae4842fa5ec3ee3f95": ("BLUE OWL CAPITAL INC CL A", "OWL", 5, 101, "2026-07-17"),
    "oge-278t:21a875558a6727b4bd24cf28": ("SLBLTD", "SLB", 5, 106, "2026-07-17"),
    "oge-278t:bd5906c0369ece848f87c75c": ("TERADYNEINC", "TER", 5, 108, "2026-07-17"),
    "oge-278t:5fd95629339329188e4c5fc4": ("Zoetislnc", "ZTS", 6, 134, "2026-07-17"),
    "oge-278t:3d7cd58f0d79f36cf8f907ef": ("Disney Walt Co", "DIS", 6, 145, "2026-07-17"),
    "oge-278t:07db8e523ed93a0df6d69be6": (". Te Connectivitv Pie", "TEL", 6, 149, "2026-07-17"),
    "oge-278t:13fd8424391c313280f9526a": ("Cme Grouo Inc", "CME", 6, 151, "2026-07-17"),
    "oge-278t:29aae8a187f25c215555d594": ("Palantir Technolnnies Inc", "PLTR", 6, 163, "2026-07-17"),
    "oge-278t:5524024a8d96413ad081557a": ("Microsoft Coro", "MSFT", 7, 169, "2026-07-17"),
    "oge-278t:d59e41965b3e363eb9d8968e": ("Nvidia Coll>", "NVDA", 7, 170, "2026-07-17"),
    "oge-278t:141fd0054d8d646bb146806d": ("UNITEDHEALTH GROUP INC", "UNH", 7, 198, "2026-07-31"),
    "oge-278t:e85ca2bab8ba56442e981af8": ("CITIZENS FINL GROCJP INC", "CFG", 9, 234, "2026-07-31"),
    "oge-278t:d5f0f712cc372b7084d259a6": ("ZOETIS INC CLASS A", "ZTS", 9, 241, "2026-07-08"),
    "oge-278t:528679fd0d6d9aae74f73d2d": ("CONSTELLATION BRANDS INC CLASS A", "STZ", 9, 245, "2026-07-08"),
    "oge-278t:e2d4bf1c0422725206c7fb97": ("0-1 GLASS INC", "OI", 10, 287, "2026-07-08"),
    "oge-278t:c256dc894365b20b4ba28da4": ("NTNL BK HLDGS CORP A CLASS A", "NBHC", 11, 316, "2026-07-29"),
    "oge-278t:f4cc685d05109f65e40a5115": ("SHENANDOAH TELECOMMUNICA", "SHEN", 11, 328, "2026-07-29"),
    "oge-278t:66537df312b02465ca96ca3c": ("EASTMAN CHEM CO", "EMN", 12, 341, "2026-07-08"),
    "oge-278t:3d595c108c213babc4fed1d5": ("DORMAN PRODS INC", "DORM", 12, 347, "2026-07-29"),
    "oge-278t:73ce53c22902d25e6a481101": ("HEALTHCARE SVCS GROUP IN", "HCSG", 12, 348, "2026-07-29"),
    "oge-278t:15e0153498968421c47c9fe7": ("OXFORD INDS INC", "OXM", 12, 351, "2026-07-08"),
    "oge-278t:f89a4fbea1b01afa6e2c545f": ("HERTZ GLOBAL HLDGS INC N", "HTZ", 12, 354, "2026-07-08"),
    "oge-278t:9d601f38a1f99bc60c7da277": ("CORVELCORP", "CRVL", 12, 360, "2026-07-29"),
    "oge-278t:36c83acf45941578ac94c19c": ("TANGER INC REIT", "SKT", 12, 362, "2026-07-29"),
    "oge-278t:ea1a3eb46fd18bca55e12f20": ("LYFT INC CLASS CLASS A", "LYFT", 13, 376, "2026-07-08"),
    "oge-278t:9432eaa8535a446e7e56e65a": ("ELLINGTON FINL INC REIT", "EFC", 13, 391, "2026-07-29"),
    "oge-278t:817bbeaa9ad74f42d0c15291": ("BENCHMARK ELECTRS INC", "BHE", 13, 394, "2026-07-29"),
    "oge-278t:ce049c6aa997d7dc1d7e8cfd": ("ANDERSONS INC", "ANDE", 13, 396, "2026-07-29"),
    "oge-278t:dae18c4e7baff3bcdfc8e7a9": ("SAFEHOLD INC REIT REIT", "SAFE", 14, 400, "2026-07-29"),
    "oge-278t:f0f92cd3e50de56ae12b825c": ("IMPINJINC", "PI", 14, 403, "2026-07-29"),
    "oge-278t:f71fd36274ad6e6478c22959": ("LTC PPTYS INC REIT", "LTC", 14, 404, "2026-07-29"),
    "oge-278t:9108556a4892f40533bb952c": ("WORTHINGTON ENTERPRI", "WOR", 14, 407, "2026-07-29"),
    "oge-278t:93ab8dbab050af98b03596ad": ("EVERUS CONSTR GROUP INC", "ECG", 14, 420, "2026-07-29"),
    "oge-278t:a207586ed125a0c81e766a5c": ("OFG BANCORP F", "OFG", 14, 421, "2026-07-29"),
    "oge-278t:6d4ba8d63acf8a207c4d012b": ("AMENTIJM HLDGS INC EQUITY", "AMTM", 14, 422, "2026-07-08"),
    "oge-278t:0d0720c7dc95de7293a46204": ("PERRIGO CO PLC F", "PRGO", 14, 424, "2026-07-29"),
    "oge-278t:d0d58e2dac760227ed87a108": ("POWELL INDS INC", "POWL", 14, 426, "2026-07-08"),
    "oge-278t:e014412f18b4114243a167e3": ("UNDER ARMOUR INC CLASS C", "UA", 15, 433, "2026-07-29"),
    "oge-278t:a2cbf04260e5c3ee9b8d6557": ("GOGOINC", "GOGO", 15, 439, "2026-07-08"),
    "oge-278t:c08e7fa18cbe5b141b245563": ("ALPHA & OMEGA SEMICOND F", "AOSL", 15, 449, "2026-07-29"),
    "oge-278t:8389b9f25c36fd48a9d4910b": ("NORTHWEST NAT HLDG CO", "NWN", 15, 452, "2026-07-29"),
    "oge-278t:f782d7f337210fc094bea6ff": ("COMSTOCK RES INC", "CRK", 15, 453, "2026-07-29"),
    "oge-278t:317bdc3671714d9c6c4da2d8": ("MAXLINEAR INC CLASS A", "MXL", 15, 454, "2026-07-08"),
    "oge-278t:a792d0f57b12f4d16c526bdd": ("MUELLER WATER PRODUC CLASS SERIES A", "MWA", 15, 456, "2026-07-08"),
    "oge-278t:44138f6ef9c36fcd83ad1dcf": ("MATTHEWS INTL CORP CLASS A", "MATW", 15, 460, "2026-07-29"),
    "oge-278t:15141742f9a9d3d335e4cb1e": ("APOLLO COML REAL ESTATE REIT", "ARI", 17, 515, "2026-07-29"),
    "oge-278t:5304fbb4c2fb3503b44495cc": ("OMNICELL INC", "OMCL", 17, 516, "2026-07-29"),
    "oge-278t:4e9fa32a2e06d6103b352447": ("• ACADEMY SPORTS & OUTDOOR", "ASO", 18, 546, "2026-07-29"),
    "oge-278t:120ae3c89d3a86175400a421": ("EPLUSINC", "PLUS", 19, 575, "2026-07-29"),
    "oge-278t:348f07f2bf7707f74a06c03d": ("ARGANINC", "AGX", 19, 576, "2026-07-08"),
    "oge-278t:86c3d3d90c13f4fa7aae4124": ("SOLARI$ ENERGY INFRSTR CLASS A", "SEI", 19, 579, "2026-07-29"),
    "oge-278t:53ee1a66f5b26f2d6ed1a7ab": ("MUELLER WATER PRODUC CI.J\\SS SERIES A", "MWA", 19, 580, "2026-07-29"),
    "oge-278t:86e61da8fdf2907c2c945eda": ("BGC GROUP INC CI.J\\SS A", "BGC", 19, 584, "2026-07-29"),
    "oge-278t:5e91e2714c8deca706659cae": ("ATI.J\\NTIC UN BANKSHARES C", "AUB", 19, 585, "2026-07-29"),
    "oge-278t:64d1f52f9133deeef5712322": ("PATRICK INDS INC", "PATK", 21, 629, "2026-07-29"),
    "oge-278t:20d89ff422dab33d977eb920": ("WESTERN UN CO", "WU", 22, 665, "2026-07-08"),
    "oge-278t:3ca89c4b06cb5b7ef84c8b66": ("COMCAST CORP NEW CLASS CLASS A", "CMCSA", 22, 693, "2026-07-27"),
    "oge-278t:b51afb66eaa7fe7e575489ca": ("CROWN CASTLE INC REIT REIT", "CCI", 24, 730, "2026-07-27"),
    "oge-278t:c3186c0b77f76fb5d2ebf245": ("CREDO TECHNOLOGY GROUP F", "CRDO", 24, 731, "2026-07-27"),
    "oge-278t:8f2bdc574feec76f7489106c": ("GENPACT LTD F", "G", 25, 782, "2026-07-24"),
    "oge-278t:8b4bfc4528754e78b9945c8b": ("ZOETIS INC CLASS A", "ZTS", 25, 784, "2026-07-24"),
    "oge-278t:26778fda9bbe7b93d326ddd3": ("CONSTELLATION BRANDS INC CLASS A", "STZ", 25, 791, "2026-07-24"),
    "oge-278t:23c98410d69fcf0038e25066": ("FOX CORP CLASS CLASS A", "FOXA", 26, 797, "2026-07-24"),
    "oge-278t:bd8bd85ea7c4e0adf3801b88": ("DISNEY WALT CO", "DIS", 26, 815, "2026-07-24"),
    "oge-278t:3c02b1f259f5f882658a8181": ("VERIZON COMMUNICATIONS I", "VZ", 26, 820, "2026-07-24"),
    "oge-278t:af1416d7d332cd8e665db18e": ("MEDTRONIC PLC F I", "MDT", 27, 840, "2026-07-08"),
    "oge-278t:6e7b127baeef19b0727fa7c2": ("CHEWY INC CLASS A", "CHWY", 28, 873, "2026-07-08"),
    "oge-278t:fc998ed361f00c1d98ed1bc8": ("CROWN CASTLE INC REIT REIT", "CCI", 28, 874, "2026-07-08"),
    "oge-278t:7742ff55227eaa7447ee0916": ("!DE.XX LABS INC", "IDXX", 28, 883, "2026-07-08"),
    "oge-278t:3322d2c8ea0feb47e8490cf9": ("VlSTRACORP", "VST", 28, 885, "2026-07-08"),
    "oge-278t:cf2fa32f7936391a3b435a56": ("ACCENTURE PLC IRELAND F CLASS A", "ACN", 29, 899, "2026-07-08"),
    "oge-278t:1fdf6bed65cef2663cb6f74a": ("MICROSOFT CORP I", "MSFT", 29, 910, "2026-07-08"),
    "oge-278t:caadbbfefc5a6789c00dd0c6": ("TKO GROUP HLDGS INC CL A", "TKO", 30, 944, "2026-07-08"),
    "oge-278t:00527b9c66d6218daa28cf6d": ("FORD MOTOR CO COM", "F", 30, 945, "2026-07-08"),
    "oge-278t:a5e8ea525773021d19148454": ("NXP SEMICONDUCTORS N V COM EUR", "NXPI", 30, 950, "2026-07-08"),
    "oge-278t:fb8db814ae185ef4c29e2dc0": ("NETAPPINC", "NTAP", 30, 954, "2026-07-08"),
    "oge-278t:112ee0316021d8a85b79c2cb": ("ALISTATE CORP", "ALL", 35, 1118, "2026-07-23"),
    "oge-278t:bc1cbf4a8274fe9e6f381ea7": ("QUEST DIAGNOSllCS INC", "DGX", 35, 1122, "2026-07-23"),
    "oge-278t:4e5366beae2e2bc5708f6d9e": ("VULCAN MATERIALS CO", "VMC", 36, 1133, "2026-07-23"),
    "oge-278t:8cced1376ffb161bd4d5d9f6": ("BOSTON SOENTIFIC CORP", "BSX", 36, 1141, "2026-07-23"),
    "oge-278t:bbaeac29d4e0be338447c0c3": ("ACCENTIJRE PLC", "ACN", 36, 1148, "2026-07-23"),
    "oge-278t:a70ca3db3d3f59d76f4dd9ba": ("INTlJITlVE SURGICAL INC", "ISRG", 36, 1150, "2026-07-23"),
    "oge-278t:bbdeec042b5efd70b77ae28f": ("NETFLIXINC", "NFLX", 36, 1153, "2026-07-23"),
    # Round 3: exact v5 source row, asset label, and transaction date.
    "oge-278t:ea83be0ab8b94c684188e5c9": ("PTCINC", "PTC", 4, 98, "2026-07-17"),
    "oge-278t:06f0fc10e93a14c850d6e4fe": ("EQtCorp", "EQT", 6, 133, "2026-07-17"),
    "oge-278t:f38fde12830a4f04a9e224e0": ("V,straCt>m", "VST", 6, 136, "2026-07-17"),
    "oge-278t:ed25ba9f99ce0a3adef4991c": ("Costco Whsl Coro", "COST", 6, 142, "2026-07-17"),
    "oge-278t:ad1cbd31e2eda01335913e7f": ("Accenture Pie Ireland", "ACN", 6, 144, "2026-07-17"),
    "oge-278t:4737da3fb3547c0a736fc033": ("Cbre Grouo Inc", "CBRE", 6, 146, "2026-07-17"),
    "oge-278t:c9e24f2e23a2e9d5e4d00373": ("Uber Techn<>k><lies Inc", "UBER", 6, 147, "2026-07-17"),
    "oge-278t:24d580c7fe2e60e4434a8dda": ("L3harris Technoloales Inc", "LHX", 6, 158, "2026-07-17"),
    "oge-278t:d7c3af271c4c076417a51ae1": ("CrhPlc", "CRH", 6, 159, "2026-07-17"),
    "oge-278t:cb385a57731f42c824055637": ("Boston Scientific Coro", "BSX", 6, 162, "2026-07-17"),
    "oge-278t:ef94d9e29f096e9ddea179a6": ("VISA INC CLASS A", "V", 9, 243, "2026-07-16"),
    "oge-278t:b7f611894cc6f9932f64adac": ("DAVE INC CLASS CLASS A", "DAVE", 11, 321, "2026-07-29"),
    "oge-278t:73d5467195613aa39fac41f7": ("ARTISAN PARTNERS ASSET M CLASS A", "APAM", 11, 327, "2026-07-08"),
    "oge-278t:227f7450b99ff9e31ca3179e": ("EZCORP INC CLASS A", "EZPW", 12, 335, "2026-07-29"),
    "oge-278t:8b3bb35e34734a1102b4e41e": ("WENDYS CO CLASS A", "WEN", 12, 338, "2026-07-29"),
    "oge-278t:683dfb6b8e8d71a5a494899d": ("RXOINC", "RXO", 13, 381, "2026-07-29"),
    "oge-278t:9320f3b0acfdad498cc9d378": ("CHEFS WHSE INC", "CHEF", 13, 386, "2026-07-29"),
    "oge-278t:cfffc04a152a316d1e5e6bfd": ("OXFORD INDS INC", "OXM", 14, 401, "2026-07-29"),
    "oge-278t:d41a0c416fa27d94000e1104": ("CUSHMAN & WAKEFIELD F", "CWK", 15, 430, "2026-07-29"),
    "oge-278t:2b6d6517628f22df998f0abc": ("KADANTINC", "KAI", 15, 434, "2026-07-29"),
    "oge-278t:44554bd2f68e7300153c8d62": ("VERSANT MEDIA GROUP INC CLASS CLASS A", "VSNT", 15, 461, "2026-07-08"),
    "oge-278t:c0db9a2f9ee5aa4f17665a5b": ("ICHOR HLDGS LTD F", "ICHR", 17, 497, "2026-07-08"),
    "oge-278t:30f506c27fd4ffa9c253811b": ("SONIC AUTOMOTIVE INC CLASS A", "SAH", 17, 513, "2026-07-29"),
    "oge-278t:d6b2a2e08d038b1164d20e1a": ("RADNETINC", "RDNT", 17, 523, "2026-07-29"),
    "oge-278t:3746e0c2dc8d051d2fa54e4a": ("UNITED NAT FOODS INC", "UNFI", 18, 535, "2026-07-29"),
    "oge-278t:578de95209e843fed0e25736": ("BRADY CORP CLASS A", "BRC", 18, 538, "2026-07-29"),
    "oge-278t:754ded874850c5e76ff2c0e7": ("INSTALLED BLDG PRODS INC", "IBP", 18, 549, "2026-07-29"),
    "oge-278t:4b6e94cba35887898ce3fee9": ("AMERICAN EAGLE OUTFITTER", "AEO", 18, 556, "2026-07-29"),
    "oge-278t:b50f7576fc96b270351a4900": ("SAUL CTRS INC REIT", "BFS", 18, 557, "2026-07-29"),
    "oge-278t:11694cb2972870de492d03e6": ("ARMSTRONG WORLD INDS INC", "AWI", 18, 560, "2026-07-08"),
    "oge-278t:9503dda2e40008218bfd5588": ("ABMINDSINC", "ABM", 18, 561, "2026-07-29"),
    "oge-278t:1ef68d31c9d3f249f1d05fe6": ("PATTERSON-UT! ENERGY INC", "PTEN", 19, 565, "2026-07-29"),
    "oge-278t:17e5aebbe6a4f2acfffc2278": ("LAUREATE ED INC", "LAUR", 20, 595, "2026-07-29"),
    "oge-278t:f248c1b0a9bbfe014a87e2fd": ("CONMEDCORP", "CNMD", 20, 604, "2026-07-08"),
    "oge-278t:ddedcb6cc99ef30c64501fd0": ("OSI SYS INC", "OSIS", 21, 634, "2026-07-29"),
    "oge-278t:dc9c3bd96f985c0b0a3855a9": ("FULLERH BCO", "FUL", 21, 646, "2026-07-29"),
    "oge-278t:f53b1de3dfd864df2300588c": ("ALKERMES PLC F", "ALKS", 21, 657, "2026-07-29"),
    "oge-278t:d65288a73f556323be70a60a": ("CHIPOTLE MEXICAN GRILL I", "CMG", 24, 732, "2026-07-27"),
    "oge-278t:c0a200f444dc5799336121cd": ("TEMPUS Al INC CLASS A", "TEM", 24, 733, "2026-07-27"),
    "oge-278t:3abf11f5e817c2073fac4851": ("GE HEALTHCARE TECHNOLOGI", "GEHC", 25, 785, "2026-07-24"),
    "oge-278t:8e122d9d4c2020d293bed852": ("TJX COS INC NEW", "TJX", 25, 790, "2026-07-24"),
    "oge-278t:d2c79a953a4d4c8166be62ae": ("DISNEY WALT CO", "DIS", 28, 863, "2026-07-08"),
    "oge-278t:011db1a3b890f38d1d7410cf": ("HUBSPOTINC", "HUBS", 28, 867, "2026-07-08"),
    "oge-278t:f0e4d069bce95910e2b59901": ("APPLOVIN CORP CLASS A", "APP", 28, 880, "2026-07-08"),
    "oge-278t:0dddaaaea100dfe1c436f5ee": ("VICI PPTYS INC REIT", "VICI", 28, 891, "2026-07-08"),
    "oge-278t:57fb5a552e51f7226f094c76": ("AMERICAN TOWER CORP NEW REIT", "AMT", 29, 894, "2026-07-08"),
    "oge-278t:0fdd5f5bb4fdfd49e81f839c": ("STERISPLCF", "STE", 29, 897, "2026-07-08"),
    "oge-278t:b6c9667b897b0d2dd8391e20": ("LAUDER ESTEE COS CL A", "EL", 30, 939, "2026-07-08"),
    "oge-278t:36ea9634e19facfa785753d2": ("CHIPOTLE MEXICAN GRILL INC CL A", "CMG", 30, 940, "2026-07-08"),
    "oge-278t:9ff0eea29f004b954f4da045": ("CISCO SYSTEMS INC", "CSCO", 30, 957, "2026-07-08"),
    "oge-278t:a4059df3778780d9ec15f032": ("AMA2ONINC", "AMZN", 33, 1039, "2026-07-20"),
    "oge-278t:6a7cf21d35562c058f2d6a05": ("CROWN CASTlE IN C", "CCI", 36, 1130, "2026-07-23"),
    "oge-278t:6edb6800bfd420ed027f7f1c": ("KROGER CO", "KR", 36, 1129, "2026-07-23"),
    # Page-7 v6 rows: inert until the separately reviewed parser promotes them.
    "oge-278t:2d10674fb4edd6281d71c093": ("Amazon Com Inc", "AMZN", 7, 168, "2026-07-17"),
    "oge-278t:608461166207f9b9f333241d": ("HOME DEPOT INC", "HD", 7, 178, "2026-07-31"),
    "oge-278t:d85eb1033dcb46f6eae29dac": ("TEXASINSTRSINC", "TXN", 7, 179, "2026-07-31"),
    "oge-278t:7c8ffd7b6242a58c7d1a5032": ("BROADCOM INC", "AVGO", 7, 180, "2026-07-31"),
    "oge-278t:956775223c4e229d063b5e3b": ("S&P GLOBAL INC", "SPGI", 7, 181, "2026-07-16"),
    "oge-278t:8aac3a79323a7895873e39e8": ("CME GROUP INC CLASS A", "CME", 7, 182, "2026-07-16"),
    "oge-278t:b943958da9dc84e6a68f9be8": ("PROCTER & GAMBLE CO", "PG", 7, 183, "2026-07-31"),
    "oge-278t:0d28d77025535d2a2e153796": ("EXXONMOBIL HLDGS CORP", "XOM", 7, 184, "2026-07-31"),
    "oge-278t:c4baa50416748b28c6a9a4f4": ("NIKE INC CLASS CLASS B", "NKE", 7, 186, "2026-07-22"),
    "oge-278t:4cedc372692338cbdc10c413": ("PHILIP MORRIS INTL INC", "PM", 7, 187, "2026-07-31"),
    "oge-278t:3a91de09bb8ecf07895a2bff": ("CISCO SYS INC", "CSCO", 7, 188, "2026-07-31"),
    "oge-278t:f36c5e73803e09c2e922f9b2": ("VERIZON COMMUNICATIONS I", "VZ", 7, 189, "2026-07-31"),
    "oge-278t:405a6b7a46fadfe90305bc1a": ("ABBOTT LABS", "ABT", 7, 190, "2026-07-31"),
    "oge-278t:6c61768be2be46745375f278": ("WATSCO INC CLASS A", "WSO", 7, 191, "2026-07-22"),
    "oge-278t:29692e04a5589bb6a7e5a9cf": ("BECTON DICKINSON & CO", "BDX", 7, 192, "2026-07-31"),
    "oge-278t:d2fb936c42e3ac4f9131fd80": ("DUKE ENERGY CORP NEW", "DUK", 7, 193, "2026-07-31"),
    "oge-278t:a7b9036a2d9aff70f4e806c4": ("FASTENAL CO", "FAST", 7, 194, "2026-07-31"),
    "oge-278t:aa271fa48fca255468def560": ("PFIZER INC", "PFE", 7, 195, "2026-07-31"),
    "oge-278t:3cd37932c7dfcbc3af1f64e9": ("ALTRIA GROUP INC", "MO", 7, 197, "2026-07-31"),
}
# Exact filing labels only. Each ticker has a prior mapped annual identity or
# issuer-published symbol evidence; the live Alpaca SIP asset must still exist.
SEMANTIC_ALIASES = {
    "ITRON INC EQUITY CLASS EQUITY": ("ITRI", ANNUAL_MAPPING_EVIDENCE),
    "AIRBNB INC CLA": ("ABNB", ANNUAL_MAPPING_EVIDENCE),
    "WORKDAY INC CLASS CLASS A": ("WDAY", ANNUAL_MAPPING_EVIDENCE),
    "META PLATFORMS INC CLASS CLASS A": ("META", ANNUAL_MAPPING_EVIDENCE),
    "AST SPACEMOBILE INC CLA": ("ASTS", ANNUAL_MAPPING_EVIDENCE),
    "[SOUTHERN CO COM": ("SO", ANNUAL_MAPPING_EVIDENCE),
    "CHARTER COMMUNICATIONS INC NEW CLA": ("CHTR", ANNUAL_MAPPING_EVIDENCE),
    "SMURFIT WESTROCK PLC F": ("SW", ANNUAL_MAPPING_EVIDENCE),
    "CONSTELLATION BRANDS INC CLA": ("STZ", ANNUAL_MAPPING_EVIDENCE),
    "VISA INC CLA": ("V", ANNUAL_MAPPING_EVIDENCE),
    "BLACKSTONE INC CLA": ("BX", ANNUAL_MAPPING_EVIDENCE),
    "PALANTIR TECHNOLOGIES INC CLA": ("PLTR", ANNUAL_MAPPING_EVIDENCE),
    "Lowes Cos Inc Com": ("LOW", ANNUAL_MAPPING_EVIDENCE),
    "WILLIAMS COS INC DEL": ("WMB", ANNUAL_MAPPING_EVIDENCE),
    "MERCK & CO INC COM": ("MRK", ANNUAL_MAPPING_EVIDENCE),
    "EMERSON ELECTRIC COM": ("EMR", ANNUAL_MAPPING_EVIDENCE),
    "WORKDAY INC CLA": ("WDAY", ANNUAL_MAPPING_EVIDENCE),
    "Boeing Co Com": ("BA", ANNUAL_MAPPING_EVIDENCE),
    "MEDTRONIC PLC F.": ("MDT", ANNUAL_MAPPING_EVIDENCE),
    "DOORDASH INC CLA": ("DASH", ANNUAL_MAPPING_EVIDENCE),
    "Arista Networks Inc Com New.": ("ANET", ANNUAL_MAPPING_EVIDENCE),
    "APPLOVIN CORP CLA": ("APP", ANNUAL_MAPPING_EVIDENCE),
    "ZOETIS INC CLA": ("ZTS", ANNUAL_MAPPING_EVIDENCE),
    "FOX CORP CLASS A": ("FOXA", ANNUAL_MAPPING_EVIDENCE),
    "BOSTON SCIENTIFIC CORP COM": ("BSX", ANNUAL_MAPPING_EVIDENCE),
    "KINDER MORGAN INC DEL": ("KMI", ANNUAL_MAPPING_EVIDENCE),
    "DOORDASH INC CLASS CLASS A": ("DASH", ANNUAL_MAPPING_EVIDENCE),
    "COPART INC": (
        "CPRT", "https://www.copart.com/content/cprt-01-31-26-earnings-release.pdf"),
    "EXXON MOBIL CORP": (
        "XOM", "https://investor.exxonmobil.com/company-information/"
        "press-releases/detail/1208/exxonmobil-announces-second-quarter-2026-results"),
    "MARSH & MCLENNAN COS INC": (
        "MRSH", "https://www.marsh.com/en/corp/about/news/"
        "marsh-mclennan-to-change-nyse-symbol-to-mrsh.html"),
    "CHESAPEAKE UTILS CORP": ("CPK", "https://www.chpk.com/investors/"),
    "BRIGHT HORIZONS FAMILY S": (
        "BFAM", "https://investors.brighthorizons.com/"),
    "GALLAGHER ARTHUR J & CO": (
        "AJG", "https://investor.ajg.com/news/news-details/2026/"
        "Arthur-J--Gallagher--Co--Announces-Second-Quarter-2026-Financial-Results/"
        "default.aspx"),
    "COGNIZANT TECHNOLOGY SOLUTIONS CORP CLA": (
        "CTSH", "https://investors.cognizant.com/investor-resources/"
        "stock-information/default.aspx"),
    "NIKE INC CLASS CLASS B": (
        "NKE", "https://investors.nike.com/investors/news-events-and-reports/"
        "investor-news/investor-news-details/2026/"
        "NIKE-Inc--Declares-0-41-Quarterly-Dividend-f1997578c/default.aspx"),
    "UNIVERSAL CORP VA": (
        "UVV", "https://investor.universalcorp.com/news/news-details/2026/"
        "Universal-Corporation-Reports-Fiscal-Year-and-Fourth-Quarter-2026-Results/"),
    "BLUE OWL CAPITAL INC CLA": (
        "OWL", "https://www.sec.gov/Archives/edgar/data/1823945/"
        "000182394526000009/owl-20251231.htm"),
    "GODADDY INC CLASS CLASS A": ("GDDY", ANNUAL_MAPPING_EVIDENCE),
    "CBRE GROUP INC CLASS CLASS A": ("CBRE", ANNUAL_MAPPING_EVIDENCE),
    "GLOBAL PMTS INC": ("GPN", ANNUAL_MAPPING_EVIDENCE),
    "CHARTER COMMUNICATIONS | CLASS A": ("CHTR", ANNUAL_MAPPING_EVIDENCE),
    "PALANTIR TECHNOLOGIES IN CLASS A": ("PLTR", ANNUAL_MAPPING_EVIDENCE),
}
FALSE_EXPLICIT = {
    ("wh-url:7e17c4be2b42f563d37df173", "CIGNA GROUP"): "THE",
    ("wh-url:01475dee0bcffa4e79f7f58c", "KROGER CO"): "THE",
    (TRUMP_SEPT_2026_DOCUMENT_ID, "KROGER CO"): "THE",
    ("wh-url:7e17c4be2b42f563d37df173", "JADOBE INC."): "DELAWARE",
    ("wh-url:7e17c4be2b42f563d37df173", "COCA COLA COMPANY"): "THE",
    ("wh-url:7e17c4be2b42f563d37df173", "HARTFORD INSURANCE GROUP INC"): "THE",
    ("wh-url:7e17c4be2b42f563d37df173", "HERSHEY COMPANY"): "THE",
}
DEBT_LABEL = re.compile(
    r"\b(?:BOND|BONDS|BDS|NOTE|NOTES|NTS|DEBENTURE|TREASURY|MUNICIPAL|"
    r"MUNI|DUE|DTD|YTM|COUPON|REFUNDING|RFDG)\b|\bB/E\b|%|@",
    re.IGNORECASE,
)


def _source_row(row: dict) -> bool:
    profile = REPORTS.get(row.get("filing_id"))
    return (profile is not None and
            str(row.get("id") or "").startswith("oge-278t:") and
            row.get("person_id") == TRUMP_PERSON_ID and
            row.get("source_id") == "oge" and
            row.get("verification_status") == "official_matched" and
            row.get("source_url") == profile["source_url"] and
            row.get("filed_at") == profile["report_date"] + "T00:00:00Z")


def _false_explicit(row: dict) -> bool:
    return (_source_row(row) and
            row.get("ticker") == FALSE_EXPLICIT.get(
                (row.get("filing_id"), row.get("asset_name"))) and
            row.get("ticker_mapping_basis") == "filing_explicit")


def _eligible_name(row: dict) -> bool:
    if not _source_row(row):
        return False
    asset_name = str(row.get("asset_name") or "")
    return (row.get("instrument_type") in {"Stock", "ETF", "Unspecified"} and
            not DEBT_LABEL.search(asset_name))


def _semantic_rule_id(asset_name: str) -> str:
    return "semantic:" + hashlib.sha256(asset_name.encode("utf-8")).hexdigest()[:16]


def _source_directory_rule_id(record_id: str, asset_name: str, ticker: str,
                              page_number: int, row_number: int,
                              transaction_date: str, directory_url: str) -> str:
    evidence = "\x00".join((record_id, asset_name, ticker, str(page_number),
                              str(row_number), transaction_date,
                              TRUMP_SEPT_2026_SOURCE_URL,
                              directory_url, SECURITY_DIRECTORY_CHECKED_ON))
    return "source-directory:" + hashlib.sha256(
        evidence.encode("utf-8")).hexdigest()[:16]


def _apply_semantic_aliases(proposed: dict, assets: list[dict],
                            ambiguous_ids: set[str]) -> list[dict]:
    registry = _asset_registry(assets)
    recovered = []
    for row in proposed["transactions"]:
        if (not _eligible_name(row) or row.get("ticker") or
                row["id"] in ambiguous_ids):
            continue
        rule = SEMANTIC_ALIASES.get(row["asset_name"])
        if rule is None:
            continue
        ticker, evidence_url = rule
        asset = registry.get(ticker)
        if (asset is None or asset["status"] != "active" or
                asset["exchange"] not in SIP_EXCHANGES):
            continue
        row["ticker"] = ticker
        row["ticker_mapping_basis"] = SEMANTIC_BASIS
        recovered.append({
            "record_id": row["id"], "asset_name": row["asset_name"],
            "ticker": ticker, "mapping_basis": SEMANTIC_BASIS,
            "provider_asset_name": asset["name"],
            "semantic_rule_id": _semantic_rule_id(row["asset_name"]),
            "semantic_evidence_url": evidence_url,
        })
    return recovered


def _unique_active_sip_asset(assets: list[dict], ticker: str) -> dict | None:
    matches = []
    for row in assets:
        if not isinstance(row, dict):
            continue
        if (str(row.get("symbol") or "").strip().upper() == ticker and
                str(row.get("class") or "").strip().casefold() == "us_equity" and
                str(row.get("status") or "").strip().casefold() == "active" and
                str(row.get("exchange") or "").strip().upper() in SIP_EXCHANGES and
                isinstance(row.get("id"), str) and row["id"].strip() and
                isinstance(row.get("name"), str) and row["name"].strip()):
            matches.append(dict(
                row, symbol=ticker,
                status=str(row["status"]).strip().casefold(),
                exchange=str(row["exchange"]).strip().upper(),
            ))
    return matches[0] if len(matches) == 1 else None


def _apply_september_source_aliases(proposed: dict, assets: list[dict]) -> list[dict]:
    """Apply only fixed official-row mappings backed by current symbol directories."""
    registry = _asset_registry(assets)
    recovered = []
    for row in proposed["transactions"]:
        if (row.get("filing_id") != TRUMP_SEPT_2026_DOCUMENT_ID or
                not _eligible_name(row) or row.get("ticker")):
            continue
        rule = SEPTEMBER_SOURCE_ALIASES.get(row["id"])
        if rule is None:
            continue
        asset_name, ticker, page_number, row_number, transaction_date = rule
        if row["asset_name"] != asset_name:
            raise ValueError("A fixed September source row changed its asset label")
        if row.get("transaction_date") != transaction_date:
            raise ValueError("A fixed September source row changed its transaction date")
        directory_name, directory_url = SECURITY_DIRECTORY[ticker]
        asset = _unique_active_sip_asset(assets, ticker)
        if asset is None or registry.get(ticker) != asset:
            continue
        row["ticker"] = ticker
        row["ticker_mapping_basis"] = SOURCE_DIRECTORY_BASIS
        recovered.append({
            "record_id": row["id"], "asset_name": row["asset_name"],
            "ticker": ticker, "mapping_basis": SOURCE_DIRECTORY_BASIS,
            "provider_asset_name": asset["name"],
            "provider_asset_id": asset["id"],
            "provider_exchange": asset["exchange"],
            "provider_active_sip_match_count": 1,
            "source_evidence_url": TRUMP_SEPT_2026_SOURCE_URL,
            "source_page_number": page_number,
            "source_row_number": row_number,
            "source_transaction_date": transaction_date,
            "security_directory_name": directory_name,
            "security_directory_url": directory_url,
            "security_directory_checked_on": SECURITY_DIRECTORY_CHECKED_ON,
            "ambiguity_exclusion_basis": SOURCE_DIRECTORY_AMBIGUITY_GUARD,
            "source_rule_id": _source_directory_rule_id(
                row["id"], row["asset_name"], ticker, page_number, row_number,
                transaction_date, directory_url),
        })
    return recovered


def _apply_prior_trump_exact_names(proposed: dict, assets: list[dict],
                                   ambiguous_ids: set[str]) -> list[dict]:
    """Reuse only a unique, already qualified Trump ticker for this fixed OGE PDF."""
    registry = _asset_registry(assets)
    prior_by_name: dict[str, dict[str, set[str]]] = {}
    for row in proposed["transactions"]:
        if (row.get("person_id") != TRUMP_PERSON_ID or
                row.get("filing_id") == TRUMP_SEPT_2026_DOCUMENT_ID or
                row.get("source_id") != "oge" or
                row.get("verification_status") != "official_matched" or
                not isinstance(row.get("ticker"), str) or
                not TICKER.fullmatch(row["ticker"]) or
                row.get("ticker_mapping_basis") not in {
                    "alpaca_unique_asset_name", "alpaca_unique_classless_asset_name",
                    SEMANTIC_BASIS,
                } or
                not isinstance(row.get("asset_name"), str)):
            continue
        prior_by_name.setdefault(row["asset_name"], {}).setdefault(
            row["ticker"], set()).add(row["id"])
    recovered = []
    for row in proposed["transactions"]:
        if (row.get("filing_id") != TRUMP_SEPT_2026_DOCUMENT_ID or
                not _eligible_name(row) or row.get("ticker") or
                row["id"] in ambiguous_ids):
            continue
        matches = prior_by_name.get(row["asset_name"], {})
        if len(matches) != 1:
            continue
        ticker, prior_ids = next(iter(matches.items()))
        asset = registry.get(ticker)
        if (asset is None or asset["status"] != "active" or
                asset["exchange"] not in SIP_EXCHANGES):
            continue
        row["ticker"] = ticker
        row["ticker_mapping_basis"] = PRIOR_TRUMP_BASIS
        recovered.append({
            "record_id": row["id"], "asset_name": row["asset_name"],
            "ticker": ticker, "mapping_basis": PRIOR_TRUMP_BASIS,
            "provider_asset_name": asset["name"],
            "prior_record_ids": sorted(prior_ids),
        })
    return recovered


def is_allowed_2026_ticker_change(before: dict, after: dict) -> bool:
    """Accept only a mapped ticker or correction of a known OCR suffix error."""
    if not _source_row(before) or not _source_row(after):
        return False
    old = (before.get("ticker"), before.get("ticker_mapping_basis"))
    new = (after.get("ticker"), after.get("ticker_mapping_basis"))
    if old == new or (old != (None, None) and not _false_explicit(before)):
        return False
    if new != (None, None) and not (
            isinstance(new[0], str) and TICKER.fullmatch(new[0]) and
            new[1] in ALLOWED_BASES and _eligible_name(after)):
        return False
    if new == (None, None) and old == (None, None):
        return False
    return all(before.get(key) == after.get(key) for key in
               (before.keys() | after.keys()) - {"ticker", "ticker_mapping_basis"})


def _previous(previous: dict | None) -> dict[str, dict]:
    if previous is None:
        return {}
    rows = previous.get("mappings")
    if previous.get("schema_version") != SCHEMA or not isinstance(rows, list):
        raise ValueError("Previous Trump 2026 ticker mapping is invalid")
    result = {}
    for row in rows:
        if (not isinstance(row, dict) or
                not isinstance(row.get("record_id"), str) or
                not isinstance(row.get("asset_name"), str) or
                not isinstance(row.get("ticker"), str) or
                not TICKER.fullmatch(row["ticker"]) or
                row.get("mapping_basis") not in ALLOWED_BASES or
                row["record_id"] in result):
            raise ValueError("Previous Trump 2026 ticker mapping row is invalid")
        if row["mapping_basis"] == SEMANTIC_BASIS:
            rule = SEMANTIC_ALIASES.get(row["asset_name"])
            if (rule is None or rule[0] != row["ticker"] or
                    row.get("semantic_rule_id") != _semantic_rule_id(row["asset_name"]) or
                    row.get("semantic_evidence_url") != rule[1]):
                raise ValueError("Previous Trump 2026 semantic rule changed")
        if row["mapping_basis"] == PRIOR_TRUMP_BASIS and not (
                isinstance(row.get("prior_record_ids"), list) and
                row["prior_record_ids"] and
                all(isinstance(item, str) and item for item in row["prior_record_ids"]) and
                row["prior_record_ids"] == sorted(set(row["prior_record_ids"]))):
            raise ValueError("Previous Trump exact-name evidence is invalid")
        if row["mapping_basis"] == SOURCE_DIRECTORY_BASIS:
            rule = SEPTEMBER_SOURCE_ALIASES.get(row["record_id"])
            if rule is None:
                raise ValueError("Previous source-directory rule no longer exists")
            if (not isinstance(row.get("provider_asset_id"), str) or
                    not row["provider_asset_id"] or
                    not isinstance(row.get("provider_asset_name"), str) or
                    not row["provider_asset_name"] or
                    not isinstance(row.get("provider_exchange"), str) or
                    row["provider_exchange"] not in SIP_EXCHANGES):
                raise ValueError("Previous source-directory provider evidence is invalid")
            asset_name, ticker, page_number, row_number, transaction_date = rule
            directory_name, directory_url = SECURITY_DIRECTORY[ticker]
            if row.get("source_transaction_date") != transaction_date:
                raise ValueError("Previous source-directory transaction date is invalid")
            expected = {
                "asset_name": asset_name, "ticker": ticker,
                "source_evidence_url": TRUMP_SEPT_2026_SOURCE_URL,
                "source_page_number": page_number,
                "source_row_number": row_number,
                "source_transaction_date": transaction_date,
                "security_directory_name": directory_name,
                "security_directory_url": directory_url,
                "security_directory_checked_on": SECURITY_DIRECTORY_CHECKED_ON,
                "ambiguity_exclusion_basis": SOURCE_DIRECTORY_AMBIGUITY_GUARD,
                "provider_active_sip_match_count": 1,
                "source_rule_id": _source_directory_rule_id(
                    row["record_id"], asset_name, ticker, page_number, row_number,
                    transaction_date, directory_url),
            }
            if any(row.get(key) != value for key, value in expected.items()):
                raise ValueError("Previous source-directory evidence changed")
        result[row["record_id"]] = row
    return result


def restore_pre_enrichment(candidate: dict, mapping_audit: dict) -> dict:
    """Recover the exact 278-T projection for an idempotent review rebuild."""
    mappings = _previous(mapping_audit)
    correction_ids = mapping_audit.get("correction_ids")
    if (not isinstance(correction_ids, list) or
            len(correction_ids) != len(set(correction_ids)) or
            mapping_audit.get("correction_count") != len(correction_ids)):
        raise ValueError("Prior Trump 2026 ticker corrections are invalid")
    result = deepcopy(candidate)
    rows = {row["id"]: row for row in result["transactions"] if _source_row(row)}
    for record_id, mapping in mappings.items():
        row = rows.get(record_id)
        if (row is None or row["asset_name"] != mapping["asset_name"] or
                row.get("ticker") != mapping["ticker"] or
                row.get("ticker_mapping_basis") != mapping["mapping_basis"]):
            raise ValueError("Prior Trump 2026 ticker mapping differs from candidate")
        row["ticker"] = None
        row["ticker_mapping_basis"] = None
    for record_id in correction_ids:
        row = rows.get(record_id)
        if (row is None or
                (row.get("filing_id"), row.get("asset_name")) not in FALSE_EXPLICIT or
                (record_id not in mappings and
                 (row.get("ticker"), row.get("ticker_mapping_basis")) != (None, None))):
            raise ValueError("Prior Trump 2026 THE correction differs from candidate")
        row["ticker"] = FALSE_EXPLICIT[(row["filing_id"], row["asset_name"])]
        row["ticker_mapping_basis"] = "filing_explicit"
    return result


def enrich_trump_2026_tickers(candidate: dict, assets: object, *,
                              checked_at: str, previous: dict | None = None
                              ) -> tuple[dict, dict]:
    """Enrich fixed 2026 PTR rows without altering identities or other facts."""
    if not isinstance(candidate, dict) or candidate.get("meta", {}).get("is_demo") is not False:
        raise ValueError("Trump 2026 ticker enrichment requires a production candidate")
    if not isinstance(assets, list) or any(not isinstance(row, dict) for row in assets):
        raise ValueError("Trump 2026 ticker enrichment requires an asset array")
    before = deepcopy(candidate)
    corrected = deepcopy(candidate)
    corrections = []
    for row in corrected.get("transactions", []):
        if _false_explicit(row):
            row["ticker"] = None
            row["ticker_mapping_basis"] = None
            corrections.append(row["id"])
    source_recovered = _apply_september_source_aliases(corrected, assets)
    proposed, recovered, current = _recover_unique_asset_name_tickers(
        corrected, assets, eligible=_eligible_name)
    recovered.extend(source_recovered)
    ambiguous_ids = {row["record_id"] for row in current["ambiguous_records"]}
    recovered.extend(_apply_semantic_aliases(proposed, assets, ambiguous_ids))
    recovered.extend(_apply_prior_trump_exact_names(proposed, assets, ambiguous_ids))
    proposed_by_id = {row["record_id"]: row for row in recovered}
    prior_by_id = _previous(previous)
    active_registry = _asset_registry(assets)
    references = {row["id"]: row for row in proposed["transactions"]
                  if isinstance(row.get("id"), str)}
    source_rule_ids = {row["record_id"] for row in recovered
                       if row["mapping_basis"] == SOURCE_DIRECTORY_BASIS}
    if (prior_by_id.keys() & ambiguous_ids) - source_rule_ids:
        raise ValueError("A sticky Trump 2026 ticker mapping is now ambiguous")
    rows_by_id = {row["id"]: row for row in proposed["transactions"] if _source_row(row)}
    mappings = []
    for record_id, old in prior_by_id.items():
        row = rows_by_id.get(record_id)
        if row is None or row["asset_name"] != old["asset_name"]:
            raise ValueError("A sticky Trump 2026 ticker mapping lost its source row")
        if old["mapping_basis"] == PRIOR_TRUMP_BASIS:
            asset = active_registry.get(old["ticker"])
            if (asset is None or asset["status"] != "active" or
                    asset["exchange"] not in SIP_EXCHANGES or
                    any((reference := references.get(prior_id)) is None or
                        reference.get("person_id") != TRUMP_PERSON_ID or
                        reference.get("source_id") != "oge" or
                        reference.get("verification_status") != "official_matched" or
                        reference.get("asset_name") != old["asset_name"] or
                        reference.get("ticker") != old["ticker"] or
                        reference.get("ticker_mapping_basis") not in {
                            "alpaca_unique_asset_name", "alpaca_unique_classless_asset_name",
                            SEMANTIC_BASIS,
                        } for prior_id in old["prior_record_ids"])):
                raise ValueError("A sticky Trump exact-name source is no longer valid")
        if old["mapping_basis"] == SOURCE_DIRECTORY_BASIS:
            asset = _unique_active_sip_asset(assets, old["ticker"])
            if (asset is None or active_registry.get(old["ticker"]) != asset or
                    row.get("transaction_date") != old["source_transaction_date"] or
                    old["provider_asset_id"] != asset["id"] or
                    old["provider_asset_name"] != asset["name"] or
                    old["provider_exchange"] != asset["exchange"]):
                raise ValueError("A sticky source-directory security is no longer active SIP")
        current_mapping = proposed_by_id.get(record_id)
        if current_mapping is not None:
            changed_keys = [key for key in (
                    "ticker", "mapping_basis", "semantic_rule_id",
                    "semantic_evidence_url", "source_rule_id",
                    "source_evidence_url", "source_page_number",
                    "source_row_number", "source_transaction_date",
                    "provider_asset_id", "provider_asset_name",
                    "provider_exchange", "provider_active_sip_match_count",
                    "ambiguity_exclusion_basis", "security_directory_name",
                    "security_directory_url", "security_directory_checked_on")
                            if current_mapping.get(key) != old.get(key)]
            if changed_keys:
                raise ValueError(
                    "Alpaca identity conflicts with a sticky Trump 2026 mapping: "
                    f"record_id={record_id}, fields={','.join(changed_keys)}"
                )
        row["ticker"] = old["ticker"]
        row["ticker_mapping_basis"] = old["mapping_basis"]
        mappings.append(dict(old))
    for record_id, current_mapping in proposed_by_id.items():
        if record_id not in prior_by_id:
            mappings.append({key: current_mapping[key] for key in (
                "record_id", "asset_name", "ticker", "mapping_basis",
                "provider_asset_name", "semantic_rule_id",
                "semantic_evidence_url", "prior_record_ids", "source_rule_id",
                "source_evidence_url", "source_page_number", "source_row_number",
                "source_transaction_date", "provider_asset_id", "provider_exchange",
                "provider_active_sip_match_count", "ambiguity_exclusion_basis",
                "security_directory_name", "security_directory_url",
                "security_directory_checked_on") if key in current_mapping})
    mappings.sort(key=lambda row: row["record_id"])
    for old, new in zip(before["transactions"], proposed["transactions"], strict=True):
        if old["id"] != new["id"]:
            raise ValueError("Trump 2026 ticker enrichment reordered transactions")
        if old != new and not is_allowed_2026_ticker_change(old, new):
            raise ValueError("Trump 2026 ticker enrichment changed a non-ticker fact")
    source_rows = list(rows_by_id.values())
    mapped_ids = {row["record_id"] for row in mappings}
    ambiguous = [row for row in current["ambiguous_records"]
                 if row["record_id"] not in mapped_ids]
    ambiguous_ids = {row["record_id"] for row in ambiguous}
    unmatched = [row for row in source_rows if not row.get("ticker") and
                 row["id"] not in ambiguous_ids]
    explicit = [row for row in source_rows if row.get("ticker") and
                row["id"] not in mapped_ids]
    if len(mapped_ids) + len(ambiguous_ids) + len(unmatched) + len(explicit) != len(source_rows):
        raise ValueError("Trump 2026 ticker counts do not conserve transactions")
    ordered_assets = sorted(assets, key=lambda row: json.dumps(
        row, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    asset_bytes = json.dumps(ordered_assets, ensure_ascii=True, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
    audit = {
        "schema_version": SCHEMA, "checked_at": checked_at,
        "candidate_before_sha256": hashlib.sha256(encode(before)).hexdigest(),
        "asset_master_sha256": hashlib.sha256(asset_bytes).hexdigest(),
        "source_transaction_count": len(source_rows),
        "correction_count": len(corrections),
        "correction_ids": sorted(corrections),
        "mapping_count": len(mappings),
        "semantic_mapping_count": sum(
            row["mapping_basis"] == SEMANTIC_BASIS for row in mappings),
        "source_directory_mapping_count": sum(
            row["mapping_basis"] == SOURCE_DIRECTORY_BASIS for row in mappings),
        "retained_mapping_count": len(prior_by_id),
        "new_mapping_count": len(mappings) - len(prior_by_id),
        "ambiguous_record_count": len(ambiguous),
        "ambiguous_records": ambiguous,
        "unmatched_record_count": len(unmatched),
        "explicit_ticker_count": len(explicit),
        "mappings": mappings,
    }
    return proposed, audit
