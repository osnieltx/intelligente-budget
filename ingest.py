import json
import os

import calendar
import requests
import pandas as pd
from datetime import date
import gspread
from google.oauth2.service_account import Credentials

# ==========================================
# CONFIGURATIONS
# ==========================================
CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
ACCOUNT_ID = os.environ.get("ACCOUNT_ID")
CREDIT_CARD_ID = os.environ.get("CREDIT_CARD_ID")

# Google Sheets Config
SPREADSHEET_NAME = 'personal-budget-ledger'
CREDENTIALS_FILE = './credentials.json'

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# ==========================================
# CUSTOM CATEGORIZATION RULES
# ==========================================
def auto_categorize_transaction(description, pluggy_category):
    desc = description.upper()

    if 'IFD*' in desc or 'IFOOD' in desc:
        return 'Food / Delivery'
    if 'UBER' in desc or '99APP' in desc or 'POSTO' in desc:
        return 'Transportation'
    if 'PAGAMENTO DE FATURA' in desc or 'PAGAMENTO FATURA' in desc:
        return 'Ignore (Credit Card Bill)'
    if 'GARAGE BIKE' in desc or 'CANOA' in desc:
        return 'Sports / Hobbies'
    if 'ENEL' in desc or 'CLARO' in desc or 'VIVO' in desc:
        return 'Fixed Bills'

    if pluggy_category:
        return f"Pluggy: {pluggy_category}"

    return 'Other / Uncategorized'

# ==========================================
# API CLIENT FUNCTIONS
# ==========================================
def get_auth_token(client_id, client_secret):
    url = "https://api.pluggy.ai/auth"
    payload = {"clientId": client_id, "clientSecret": client_secret}
    response = requests.post(url, json=payload)
    response.raise_for_status()
    return response.json().get('apiKey')

def fetch_transactions_from_api(api_key, account_id, start_date, end_date):
    url = "https://api.pluggy.ai/transactions"
    headers = {"X-API-KEY": api_key}
    params = {"accountId": account_id, "from": start_date, "to": end_date}

    results = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        params['page'] = page
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        results += data.get('results', [])
        total_pages = data.get('totalPages', 1)
        page += 1
    return results

# ==========================================
# GOOGLE SHEETS STORAGE MANAGEMENT
# ==========================================
def get_or_create_monthly_worksheet(spreadsheet, sheet_title):
    try:
        worksheet = spreadsheet.worksheet(sheet_title)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=sheet_title, rows=100, cols=10)
        # Initialize headers
        worksheet.append_row(['id', 'date', 'description', 'amount', 'category'])
    return worksheet

def load_google_sheet_data(worksheet):
    records = worksheet.get_all_records()
    if records:
        df = pd.DataFrame(records)
        df['id'] = df['id'].astype(str)
        return df
    return pd.DataFrame(columns=['id', 'date', 'description', 'amount', 'category'])

def save_to_google_sheet(worksheet, df):
    # Convert numerical amounts to standard floats/strings for the API
    df['amount'] = df['amount'].astype(float)

    # Prepare matrix with headers
    values = [df.columns.tolist()] + df.values.tolist()

    # Clear and overwrite the current month's tab
    worksheet.clear()
    worksheet.update(values, 'A1')

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    print("Initializing Google Sheets connection...")
    # Check if running in GitHub Actions (or any cloud env with the secret set)
    gcp_sa_key = os.getenv("GCP_SA_KEY")
    if gcp_sa_key:
        # Load credentials from the GitHub Actions environment secret
        service_account_info = json.loads(gcp_sa_key)
        credentials = Credentials.from_service_account_info(
            service_account_info, scopes=SCOPES
        )
        gc = gspread.authorize(credentials)
    else:
        # Fallback for local development on your machine
        gc = gspread.service_account(filename="credentials.json")
    spreadsheet = gc.open(SPREADSHEET_NAME)

    now = date.today()
    current_month_str = now.strftime('%Y-%m')
    first_of_month = date(now.year, now.month, day=1).strftime('%Y-%m-%d')
    _, last_day = calendar.monthrange(now.year, now.month)
    last_of_month = date(now.year, now.month, day=last_day).strftime('%Y-%m-%d')

    worksheet = get_or_create_monthly_worksheet(spreadsheet, current_month_str)

    print("Authenticating with Pluggy...")
    try:
        api_key = get_auth_token(CLIENT_ID, CLIENT_SECRET)
        print(f"Fetching transactions starting from {first_of_month}...")

        checking_txs = fetch_transactions_from_api(api_key, ACCOUNT_ID, first_of_month, last_of_month)
        cc_txs = fetch_transactions_from_api(api_key, CREDIT_CARD_ID, first_of_month, last_of_month)

        for tx in cc_txs:
            tx['amount'] = -tx.get('amount', 0)

        all_api_txs = checking_txs + cc_txs

        # Build DataFrame from fresh API pull
        api_data_list = []
        for tx in all_api_txs:
            api_data_list.append({
                'id': str(tx.get('id')),
                'date': tx.get('date')[:10],
                'description': tx.get('description', ''),
                'amount': float(tx.get('amount', 0.0)),
                'category': auto_categorize_transaction(tx.get('description', ''), tx.get('category'))
            })
        df_api = pd.DataFrame(api_data_list)

        # Load cloud state to preserve user edits
        df_cloud = load_google_sheet_data(worksheet)

        if not df_api.empty:
            if not df_cloud.empty:
                df_cloud_historical = df_cloud[~df_cloud['id'].isin(df_api['id'])]
                df_cloud_overlapping = df_cloud[df_cloud['id'].isin(df_api['id'])]
                df_api_new_only = df_api[~df_api['id'].isin(df_cloud_overlapping['id'])]

                df_final = pd.concat([df_cloud_historical, df_cloud_overlapping, df_api_new_only], ignore_index=True)
            else:
                df_final = df_api
        else:
            df_final = df_cloud

        if df_final.empty:
            print("No records to process.")
            return

        df_final = df_final.sort_values(by='date', ascending=False).reset_index(drop=True)

        save_to_google_sheet(worksheet, df_final)
        print(f"✔️ Google Sheet tab '{current_month_str}' synchronized successfully.")

        # Active Outflow Summary
        df_report = df_final[~df_final['category'].str.contains('Ignore', case=False, na=False)]

        print("\n" + "="*50)
        print(f"📊 EXPENSE SUMMARY - {now.strftime('%B %Y').upper()}")
        print("="*50)

        category_summary = df_report.groupby('category')['amount'].sum().sort_values(ascending=True)
        for cat, val in category_summary.items():
            print(f"{cat.ljust(30)} | R$ {val:,.2f}")
        print("-" * 50)
        print(f"TOTAL ACTIVE OUTFLOW:          | R$ {category_summary.sum():,.2f}")
        print("=" * 50)

    except Exception as e:
        print(f"Processing Error: {e}")

if __name__ == "__main__":
    main()
