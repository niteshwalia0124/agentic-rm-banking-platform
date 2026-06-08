"""
Seeds BigQuery with realistic mock data for the FSI-RM PoC.
Creates 3 RMs, 50 clients, and full financial data.

Run: python scripts/seed_bigquery.py
"""

import os
import uuid
import random
from datetime import date, timedelta
from google.cloud import bigquery

PROJECT = os.getenv("GCP_PROJECT", "your-project")
DATASET = os.getenv("BQ_DATASET", "fsi_rm_poc")
bq = bigquery.Client(project=PROJECT)

def rand_date(start_year=2020, end_year=2025) -> date:
    start = date(start_year, 1, 1)
    end = date(end_year, 12, 31)
    return start + timedelta(days=random.randint(0, (end - start).days))

def rand_inr(low, high):
    return round(random.uniform(low, high), 2)

RM_IDS = ["RM001", "RM002", "RM003"]
CITIES = ["Mumbai", "Delhi", "Bengaluru", "Chennai", "Hyderabad", "Pune", "Ahmedabad", "Jaipur",
          "Kolkata", "Kochi", "Coimbatore", "Vijayawada", "Surat", "Chandigarh"]

# City → likely preferred language (reflects real demographics)
CITY_LANGUAGE = {
    "Chennai":     "ta-IN",
    "Coimbatore":  "ta-IN",
    "Hyderabad":   "te-IN",
    "Vijayawada":  "te-IN",
    "Bengaluru":   "kn-IN",
    "Kochi":       "ml-IN",
    "Pune":        "mr-IN",
    "Kolkata":     "bn-IN",
    "Ahmedabad":   "gu-IN",
    "Surat":       "gu-IN",
    "Chandigarh":  "pa-IN",
    "Mumbai":      "hi-IN",   # diverse — Hindi as lingua franca
    "Delhi":       "hi-IN",
    "Jaipur":      "hi-IN",
}
SEGMENTS = ["HNI", "MassAffluent", "SME"]
RISKS = ["Conservative", "Moderate", "Aggressive"]
MF_FUNDS = [
    ("Mirae Asset Large Cap Fund", "Mirae Asset", "Equity"),
    ("HDFC Mid-Cap Opportunities", "HDFC AMC", "Equity"),
    ("SBI Bluechip Fund", "SBI Funds", "Equity"),
    ("Axis Liquid Fund", "Axis AMC", "Liquid"),
    ("ICICI Pru Balanced Advantage", "ICICI Pru AMC", "Hybrid"),
    ("Kotak Gilt Fund", "Kotak Mahindra AMC", "Debt"),
]
STOCKS = [
    ("INE009A01021", "Infosys Ltd", "NSE"),
    ("INE040A01034", "HDFC Bank Ltd", "NSE"),
    ("INE467B01029", "Reliance Industries", "NSE"),
    ("INE585B01010", "TCS Ltd", "NSE"),
    ("INE062A01020", "ICICI Bank Ltd", "NSE"),
]
LOAN_TYPES = ["HomeLoan", "AutoLoan", "PersonalLoan", "BusinessLoan"]

clients_rows = []
accounts_rows = []
transactions_rows = []
kyc_rows = []
crm_rows = []
mf_rows = []
sip_rows = []
loan_rows = []
demat_rows = []

FIRST_NAMES = ["Rahul", "Priya", "Amit", "Sunita", "Vikram", "Anita", "Rajesh", "Meena",
               "Sanjay", "Kavita", "Arun", "Deepa", "Suresh", "Nisha", "Manoj", "Rekha"]
LAST_NAMES = ["Sharma", "Gupta", "Patel", "Singh", "Mehta", "Joshi", "Kumar", "Shah",
              "Verma", "Agarwal", "Nair", "Reddy", "Iyer", "Bose", "Pillai", "Rao"]

for i in range(50):
    cid = f"C{str(i+1).zfill(4)}"
    name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
    segment = random.choice(SEGMENTS)
    aum = rand_inr(500000, 50000000) if segment == "HNI" else rand_inr(100000, 5000000)
    rm = random.choice(RM_IDS)
    last_contact = date.today() - timedelta(days=random.randint(1, 90))

    city = random.choice(list(CITY_LANGUAGE.keys()))
    preferred_language = CITY_LANGUAGE[city]

    clients_rows.append({
        "client_id": cid, "full_name": name, "segment": segment,
        "risk_profile": random.choice(RISKS), "rm_id": rm,
        "mobile": f"9{random.randint(100000000, 999999999)}",
        "email": f"{name.lower().replace(' ', '.')}@email.com",
        "date_of_birth": rand_date(1955, 1990).isoformat(),
        "anniversary_date": rand_date(1985, 2010).isoformat() if random.random() > 0.3 else None,
        "city": city, "relationship_since": rand_date(2010, 2022).isoformat(),
        "total_aum_inr": aum, "last_contact_date": last_contact.isoformat(),
        "preferred_language": preferred_language,
    })

    # Accounts
    for acc_type in random.sample(["Savings", "Current", "FD"], k=random.randint(1, 3)):
        accounts_rows.append({
            "account_id": str(uuid.uuid4()), "client_id": cid,
            "account_number": f"ACC{random.randint(10000000, 99999999)}",
            "account_type": acc_type,
            "balance_inr": rand_inr(10000, 5000000),
            "currency": "INR",
            "last_transaction_date": (date.today() - timedelta(days=random.randint(0, 30))).isoformat(),
            "status": "active",
            "nomination_updated": random.random() > 0.3,
        })

    # Transactions (3-5 per client)
    for _ in range(random.randint(3, 5)):
        amt = rand_inr(100000, 5000000)
        transactions_rows.append({
            "txn_id": str(uuid.uuid4()), "client_id": cid,
            "account_number": f"ACC{random.randint(10000000, 99999999)}",
            "txn_date": (date.today() - timedelta(days=random.randint(0, 30))).isoformat(),
            "txn_type": random.choice(["Credit", "Debit"]),
            "amount_inr": amt,
            "description": random.choice(["UPI transfer", "NEFT received", "FD maturity", "SIP debit", "Salary credit"]),
            "channel": random.choice(["NetBanking", "UPI", "NEFT", "Branch"]),
        })

    # KYC
    for doc in ["PAN", "Aadhaar", "Address", "Income"]:
        exp = date.today() + timedelta(days=random.randint(-10, 180))
        kyc_rows.append({
            "doc_id": str(uuid.uuid4()), "client_id": cid, "document_type": doc,
            "status": "verified" if exp > date.today() else "expired",
            "expiry_date": exp.isoformat(),
            "last_updated": rand_date(2022, 2024).isoformat(),
        })

    # CRM Interactions
    for _ in range(random.randint(1, 4)):
        crm_rows.append({
            "interaction_id": str(uuid.uuid4()), "client_id": cid,
            "interaction_date": (date.today() - timedelta(days=random.randint(0, 120))).isoformat(),
            "interaction_type": random.choice(["Call", "Email", "Meeting", "WhatsApp"]),
            "channel": random.choice(["Phone", "Gmail", "Branch", "WhatsApp"]),
            "summary": random.choice([
                "Discussed SIP renewal, client agreed to increase by ₹5000/month",
                "Portfolio review meeting, discussed equity rebalancing",
                "Client called about EMI date change, resolved",
                "Sent market update email, no response yet",
            ]),
            "rm_name": f"RM {rm}", "outcome": random.choice(["Positive", "Neutral", "Needs follow-up"]),
        })

    # MF Holdings
    for fund_name, amc, scheme_type in random.sample(MF_FUNDS, k=random.randint(1, 3)):
        invested = rand_inr(50000, 1000000)
        nav_gain = random.uniform(0.85, 1.4)
        mf_rows.append({
            "holding_id": str(uuid.uuid4()), "client_id": cid,
            "fund_name": fund_name, "amc_name": amc, "scheme_type": scheme_type,
            "units": round(invested / 100, 3), "purchase_nav": 100.0,
            "current_nav": round(100 * nav_gain, 2),
            "current_value_inr": round(invested * nav_gain, 2),
            "invested_amount_inr": invested,
            "as_of_date": (date.today() - timedelta(days=1)).isoformat(),
        })

        # SIP for some
        if random.random() > 0.4:
            expiry = date.today() + timedelta(days=random.randint(-5, 60))
            sip_rows.append({
                "sip_id": str(uuid.uuid4()), "client_id": cid, "fund_name": fund_name,
                "monthly_amount_inr": rand_inr(5000, 50000),
                "next_debit_date": (date.today() + timedelta(days=random.randint(1, 30))).isoformat(),
                "expiry_date": expiry.isoformat(),
                "start_date": rand_date(2021, 2024).isoformat(),
                "status": "active" if expiry >= date.today() else "expired",
            })

    # Loans
    if random.random() > 0.4:
        loan_type = random.choice(LOAN_TYPES)
        sanctioned = rand_inr(500000, 20000000)
        outstanding = rand_inr(sanctioned * 0.3, sanctioned)
        dpd = random.choices([0, 0, 0, 15, 45, 75], weights=[60, 15, 10, 8, 5, 2])[0]
        loan_rows.append({
            "loan_id": str(uuid.uuid4()), "client_id": cid, "loan_type": loan_type,
            "sanctioned_amount_inr": sanctioned, "outstanding_inr": outstanding,
            "emi_amount_inr": rand_inr(10000, 150000),
            "next_emi_date": (date.today() + timedelta(days=random.randint(1, 30))).isoformat(),
            "dpd_days": dpd, "ltv_ratio": round(random.uniform(50, 90), 1),
            "interest_rate_pct": round(random.uniform(8.5, 14.5), 2),
            "maturity_date": (date.today() + timedelta(days=random.randint(365, 3650))).isoformat(),
            "collateral_value_inr": sanctioned * 1.3 if loan_type in ["HomeLoan", "BusinessLoan"] else None,
            "status": "active",
        })

    # Demat holdings
    for isin, company, exchange in random.sample(STOCKS, k=random.randint(0, 3)):
        qty = random.randint(10, 500)
        buy_price = rand_inr(500, 5000)
        curr_price = buy_price * random.uniform(0.7, 2.5)
        demat_rows.append({
            "holding_id": str(uuid.uuid4()), "client_id": cid,
            "isin": isin, "company_name": company, "exchange": exchange,
            "quantity": qty, "avg_buy_price": round(buy_price, 2),
            "current_price": round(curr_price, 2),
            "current_value_inr": round(qty * curr_price, 2),
            "unrealized_pnl_inr": round(qty * (curr_price - buy_price), 2),
            "as_of_date": (date.today() - timedelta(days=1)).isoformat(),
        })

def insert(table: str, rows: list[dict]):
    if not rows:
        return
    ref = f"{PROJECT}.{DATASET}.{table}"
    errors = bq.insert_rows_json(ref, rows)
    if errors:
        print(f"  ERROR inserting into {table}: {errors[:2]}")
    else:
        print(f"  Inserted {len(rows)} rows → {table}")

print("Seeding BigQuery mock data...")
for table, rows in [
    ("clients", clients_rows),
    ("accounts", accounts_rows),
    ("transactions", transactions_rows),
    ("kyc_documents", kyc_rows),
    ("crm_interactions", crm_rows),
    ("mf_holdings", mf_rows),
    ("sip_mandates", sip_rows),
    ("loans", loan_rows),
    ("demat_holdings", demat_rows),
]:
    insert(table, rows)

print("Done! Run: python -m pytest tests/ to validate.")
