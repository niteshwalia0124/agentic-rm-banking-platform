-- BigQuery schema for FSI-RM PoC mock dataset
-- Dataset: fsi_rm_poc
-- Run: bq mk --dataset $GCP_PROJECT:fsi_rm_poc
-- Then: bq query --use_legacy_sql=false < data/bigquery_schema.sql

CREATE TABLE IF NOT EXISTS `fsi_rm_poc.clients` (
  client_id STRING NOT NULL,
  full_name STRING,
  segment STRING,           -- HNI | MassAffluent | SME
  risk_profile STRING,      -- Conservative | Moderate | Aggressive
  rm_id STRING,
  mobile STRING,
  email STRING,
  date_of_birth DATE,
  anniversary_date DATE,
  city STRING,
  relationship_since DATE,
  total_aum_inr FLOAT64,
  last_contact_date DATE,
  preferred_language STRING  -- BCP-47 code: hi-IN | ta-IN | te-IN | kn-IN | ml-IN | mr-IN | bn-IN | gu-IN | pa-IN | en-IN
);

CREATE TABLE IF NOT EXISTS `fsi_rm_poc.accounts` (
  account_id STRING NOT NULL,
  client_id STRING,
  account_number STRING,
  account_type STRING,      -- Savings | Current | FD | RD
  balance_inr FLOAT64,
  currency STRING DEFAULT 'INR',
  last_transaction_date DATE,
  status STRING,            -- active | dormant | closed
  nomination_updated BOOL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS `fsi_rm_poc.transactions` (
  txn_id STRING NOT NULL,
  client_id STRING,
  account_number STRING,
  txn_date DATE,
  txn_type STRING,          -- Credit | Debit
  amount_inr FLOAT64,
  description STRING,
  channel STRING            -- Branch | NetBanking | UPI | NEFT | RTGS
);

CREATE TABLE IF NOT EXISTS `fsi_rm_poc.kyc_documents` (
  doc_id STRING NOT NULL,
  client_id STRING,
  document_type STRING,     -- PAN | Aadhaar | Address | Income | Photo
  status STRING,            -- verified | pending | expired | missing
  expiry_date DATE,
  last_updated DATE
);

CREATE TABLE IF NOT EXISTS `fsi_rm_poc.crm_interactions` (
  interaction_id STRING NOT NULL,
  client_id STRING,
  interaction_date DATE,
  interaction_type STRING,  -- Call | Email | Meeting | WhatsApp
  channel STRING,
  summary STRING,
  rm_name STRING,
  outcome STRING            -- Positive | Neutral | Needs follow-up
);

CREATE TABLE IF NOT EXISTS `fsi_rm_poc.mf_holdings` (
  holding_id STRING NOT NULL,
  client_id STRING,
  fund_name STRING,
  amc_name STRING,
  scheme_type STRING,       -- Equity | Debt | Hybrid | Liquid
  units FLOAT64,
  purchase_nav FLOAT64,
  current_nav FLOAT64,
  current_value_inr FLOAT64,
  invested_amount_inr FLOAT64,
  as_of_date DATE
);

CREATE TABLE IF NOT EXISTS `fsi_rm_poc.sip_mandates` (
  sip_id STRING NOT NULL,
  client_id STRING,
  fund_name STRING,
  monthly_amount_inr FLOAT64,
  next_debit_date DATE,
  expiry_date DATE,
  start_date DATE,
  status STRING             -- active | paused | expired | cancelled
);

CREATE TABLE IF NOT EXISTS `fsi_rm_poc.loans` (
  loan_id STRING NOT NULL,
  client_id STRING,
  loan_type STRING,         -- HomeLoan | AutoLoan | PersonalLoan | BusinessLoan | LAP
  sanctioned_amount_inr FLOAT64,
  outstanding_inr FLOAT64,
  emi_amount_inr FLOAT64,
  next_emi_date DATE,
  dpd_days INT64 DEFAULT 0, -- Days Past Due
  ltv_ratio FLOAT64,        -- Loan-to-Value (for secured loans)
  interest_rate_pct FLOAT64,
  maturity_date DATE,
  collateral_value_inr FLOAT64,
  status STRING             -- active | closed | npa
);

CREATE TABLE IF NOT EXISTS `fsi_rm_poc.demat_holdings` (
  holding_id STRING NOT NULL,
  client_id STRING,
  isin STRING,
  company_name STRING,
  exchange STRING,          -- NSE | BSE
  quantity INT64,
  avg_buy_price FLOAT64,
  current_price FLOAT64,
  current_value_inr FLOAT64,
  unrealized_pnl_inr FLOAT64,
  as_of_date DATE
);

-- Staging table for all outbound communications (email, WhatsApp)
-- Nothing sends without RM approval (RBI FREE-AI human-in-loop requirement)
CREATE TABLE IF NOT EXISTS `fsi_rm_poc.comms_drafts` (
  draft_id STRING NOT NULL,
  client_id STRING,
  rm_id STRING,
  channel STRING,           -- email | whatsapp
  communication_type STRING,
  to_address STRING,
  subject STRING,
  body STRING,
  status STRING,            -- pending_rm_approval | approved | discarded | sent
  created_at TIMESTAMP,
  approved_at TIMESTAMP,
  sent_at TIMESTAMP
);
