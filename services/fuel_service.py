import pandas as pd
import io
import re


# =========================
# PPT
# =========================

def transform_ppt(contents):

    df = pd.read_excel(io.BytesIO(contents), skiprows=19)

    df = df.dropna(subset=['Card no.'])

    df = df.iloc[:, [0, 15, 16, 22, 13]]

    df.iloc[:, 0] = df.iloc[:, 0].astype(str)

    df = df[~df.iloc[:, 0].str.startswith('Department:')]

    header_col_3 = df.columns[4]

    valid_fuel_types = [
        "DIESEL",
        "HI DIESEL S",
        "HI DIESEL S B10",
        "HI DIESEL S B7",
        "HI PREMIUM DIESEL S B7",
        "GASOHOL E20S EVO",
        "NGV"
    ]

    if df.iloc[0, 4] in valid_fuel_types:
        df.iloc[0, 4] = header_col_3

    for i in range(1, len(df)):
        if df.iloc[i, 4] in valid_fuel_types:
            df.iloc[i, 4] = df.iloc[i - 1, 4]

    df = df[~df.iloc[:, 0].str.startswith('Card no.')]

    new_column_names = {
        0: 'Date',
        1: 'amount_DIESEL',
        2: 'amount_NGV',
        3: 'price',
        4: 'number_plate'
    }

    df.rename(columns={
        df.columns[i]: new_name
        for i, new_name in new_column_names.items()
    }, inplace=True)

    df['fuel_type'] = df['amount_NGV'].apply(lambda x: 'DISEL' if x == 0 else 'NGV')

    df['amount'] = df['amount_NGV'] + df['amount_DIESEL']

    df['number_plate'] = df['number_plate'].str.replace('Plate No. ', '', regex=False)

    df['number_plate'] = df['number_plate'].str.replace('สบ', '', regex=False)
    df['number_plate'] = df['number_plate'].str.strip()
    df['number_plate'] = df['number_plate'].str.replace(" ", "", regex=False)


    df = df[['Date', 'amount', 'price', 'number_plate', 'amount_DIESEL', 'amount_NGV']]

    df['Date'] = df['Date'].astype(str).str.split(' ').str[0]

    return df


# =========================
# Bangchak
# =========================

def transform_bangchak(contents):

    df = pd.read_excel(io.BytesIO(contents), skiprows=18)

    df = df.dropna(subset=['Card no.'])

    df = df.iloc[:, [0, 15, 22, 13]]

    df.iloc[:, 0] = df.iloc[:, 0].astype(str)

    df = df[~df.iloc[:, 0].str.startswith('Department:')]

    header_col_3 = df.columns[3]

    valid_types = [
        "HI DIESEL S",
        "HI DIESEL S B10",
        "HI DIESEL S B7",
        "HI PREMIUM DIESEL S B7",
        "GASOHOL E20S EVO"
    ]

    if df.iloc[0, 3] in valid_types:
        df.iloc[0, 3] = header_col_3

    for i in range(1, len(df)):
        if df.iloc[i, 3] in valid_types:
            df.iloc[i, 3] = df.iloc[i - 1, 3]

    df = df[~df.iloc[:, 0].str.startswith('Card no.')]

    new_column_names = {
        0: 'Date',
        1: 'amount',
        2: 'price',
        3: 'number_plate'
    }

    df.rename(columns={
        df.columns[i]: new_name
        for i, new_name in new_column_names.items()
    }, inplace=True)

    df['number_plate'] = df['number_plate'].str.replace('Plate No. ', '', regex=False)

    df['number_plate'] = df['number_plate'].str.replace('สบ', '', regex=False)

    df['Date'] = df['Date'].astype(str).str.split(' ').str[0]

    return df


# =========================
# Caltex
# =========================

def transform_caltex(contents):

    df = pd.read_excel(io.BytesIO(contents))

    df = df.iloc[:, [3, 16, 17, 12]]

    new_column_names = {
        0: 'Date',
        1: 'amount',
        2: 'price',
        3: 'number_plate'
    }

    df.rename(columns={
        df.columns[i]: new_name
        for i, new_name in new_column_names.items()
    }, inplace=True)

    df['Date'] = df['Date'].astype(str).str.split(' ').str[0]

    df['Date'] = pd.to_datetime(df['Date'], format='%d/%m/%Y').dt.strftime('%Y-%m-%d')

    return df


# =========================
# Saraburi / Rayong
# =========================

def transform_meter(contents):

    df = pd.read_excel(io.BytesIO(contents), sheet_name='เลขมิตเตอร์')

    keywords = [
        'วันที่',
        'ทะเบียนรถ',
        'พจส./พจร/จนท',
        'มิเตอร์ ก่อนเติม',
        'มิเตอร์หลังเติม',
        'จำนวนลิตรที่เติม',
        'รับเพิ่ม',
        'ยอดคงเหลือ'
    ]

    df = df.filter(regex='|'.join(keywords))

    df = df.iloc[:, -8:]

    df = df.iloc[:, [0, 1, 5]]

    new_column_names = {
        0: 'Date',
        1: 'number_plate',
        2: 'amount'
    }

    df.rename(columns={
        df.columns[i]: new_name
        for i, new_name in new_column_names.items()
    }, inplace=True)

    df.dropna(subset=['Date'], inplace=True)

    def convert_date(date_str):

        if isinstance(date_str, str):

            if re.match(r'^\d{4}-\d{2}-\d{2}', date_str):
                return date_str.split(' ')[0]

            match = re.match(r'(\d+)/(\d+)/(\d+)\(\d+\)', date_str)

            if match:
                d, m, y = match.groups()
                return f"{y}-{int(m):02d}-{int(d):02d}"

        if isinstance(date_str, pd.Timestamp):
            return date_str.strftime('%Y-%m-%d')

        return date_str

    df['Date'] = df['Date'].apply(convert_date)

    df['Date'] = df['Date'].astype(str).str.split(' ').str[0]

    return df