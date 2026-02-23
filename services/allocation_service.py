import pandas as pd
import numpy as np
from io import BytesIO


# ============================================================
# BALANCED ALLOCATION
# ============================================================

def allocate_with_balance(df, cost_col, ratio_col='สัดส่วน'):

    df = df.copy()

    df['_allocated'] = np.where(
        df[ratio_col] == 0,
        df[cost_col],
        df[ratio_col] * df[cost_col]
    )

    group_sum = df.groupby('หาง')['_allocated'].transform('sum')
    diff = df[cost_col] - group_sum

    first_mask = df.groupby('หาง').cumcount() == 0
    df.loc[first_mask, '_allocated'] += diff[first_mask]

    return df['_allocated']


# ============================================================
# CORE LOGIC
# ============================================================

def process_files(ldt_file, gpm_file, cost_file):

    # ----------------------------
    # LOAD LDT
    # ----------------------------
    ldt = pd.read_excel(ldt_file, skiprows=1)
    ldt = ldt[['หัว', 'หาง', 'ค่าจัดส่ง']]

    ldt['หัว'] = ldt['หัว'].astype(str).str.strip()
    ldt['หาง'] = ldt['หาง'].astype(str).str.strip()
    ldt['ค่าจัดส่ง'] = ldt['ค่าจัดส่ง'].astype(float)

    grouped = ldt.groupby(['หัว', 'หาง'], as_index=False)['ค่าจัดส่ง'].sum()
    trailer_sum = ldt.groupby('หาง', as_index=False)['ค่าจัดส่ง'].sum()

    df_ratio = grouped.merge(
        trailer_sum,
        on='หาง',
        how='left',
        suffixes=('_pair', '_total')
    )

    df_ratio['ค่าจัดส่ง_total'] = df_ratio['ค่าจัดส่ง_total'].fillna(0)

    df_ratio['สัดส่วน'] = np.divide(
        df_ratio['ค่าจัดส่ง_pair'],
        df_ratio['ค่าจัดส่ง_total'],
        out=np.zeros(len(df_ratio)),
        where=df_ratio['ค่าจัดส่ง_total'] != 0
    )

    sum_ratio = df_ratio.groupby('หาง')['สัดส่วน'].transform('sum')
    df_ratio['สัดส่วน'] = np.where(
        sum_ratio != 0,
        df_ratio['สัดส่วน'] / sum_ratio,
        0.0
    )

    df_ratio['หัว-หาง'] = df_ratio['หัว'] + "-" + df_ratio['หาง']

    # ----------------------------
    # LOAD COST
    # ----------------------------
    cost = pd.read_excel(cost_file, sheet_name='all')
    cost['ทะเบียน'] = cost['ทะเบียน'].astype(str).str.strip()

    cost_cols = ['ทะเบียน', 'ค่าอะไหล่', 'ยาง', 'PM', 'อุบัติเหตุ']
    cost = cost[cost_cols].fillna(0)

    # =========================================================
    # PROCESS GPM
    # =========================================================
    def process_sheet(sheet_name):

        df = pd.read_excel(gpm_file, sheet_name=sheet_name, skiprows=2)
        df = df[['หัว', 'หาง']]

        df['หัว'] = df['หัว'].ffill()
        df['หัว'] = df['หัว'].astype(str).str.strip()
        df['หาง'] = df['หาง'].astype(str).str.strip()
        df['หัว-หาง'] = df['หัว'] + "-" + df['หาง']

        df = df.merge(
            df_ratio[['หัว-หาง', 'สัดส่วน']],
            on='หัว-หาง',
            how='left'
        ).fillna({'สัดส่วน': 0})

        # Trailer cost
        df = df.merge(cost, left_on='หาง', right_on='ทะเบียน', how='left').fillna(0)

        df['ค่าอะไหล่-หาง'] = allocate_with_balance(df, 'ค่าอะไหล่')
        df['ยาง-หาง'] = allocate_with_balance(df, 'ยาง')
        df['อุบัติเหตุ-หาง'] = allocate_with_balance(df, 'อุบัติเหตุ')

        # Head cost
        df = df.merge(
            cost,
            left_on='หัว',
            right_on='ทะเบียน',
            how='left',
            suffixes=('', '_หัว')
        ).fillna(0)

        count_head = df['หัว'].value_counts()
        df['count_head'] = df['หัว'].map(count_head)

        for col in ['ค่าอะไหล่', 'ยาง', 'PM', 'อุบัติเหตุ']:
            df[f'{col}-หัว'] = df[f'{col}_หัว'] / df['count_head']

        return df

    tdm = process_sheet('tdm')
    trailer = process_sheet('Trailer')

    # =========================================================
    # COST WITH FLEET
    # =========================================================

    tdm_head = set(tdm['หัว'])
    tdm_tail = set(tdm['หาง'])
    trailer_head = set(trailer['หัว'])
    trailer_tail = set(trailer['หาง'])

    fleets = []
    positions = []

    for reg in cost['ทะเบียน']:

        f = []
        p = []

        if reg in tdm_head:
            f.append("TDM")
            p.append("หัว")

        if reg in tdm_tail:
            f.append("TDM")
            p.append("หาง")

        if reg in trailer_head:
            f.append("Trailer")
            p.append("หัว")

        if reg in trailer_tail:
            f.append("Trailer")
            p.append("หาง")

        if not f:
            fleets.append("NotFound")
            positions.append("None")
        else:
            fleets.append(",".join(sorted(set(f))))
            positions.append(",".join(sorted(set(p))))

    cost_with_fleet = cost.copy()
    cost_with_fleet['Fleet'] = fleets
    cost_with_fleet['Position'] = positions

    # =========================================================
    # RECHECK TOTAL
    # =========================================================

    combined_map = {
        'TDM': tdm,
        'Trailer': trailer
    }

    summary_rows = []

    for fleet_name in ['TDM', 'Trailer']:

        cost_fleet = cost_with_fleet[
            cost_with_fleet['Fleet'].str.contains(fleet_name)
        ]

        alloc_df = combined_map[fleet_name]

        for metric in ['ค่าอะไหล่', 'PM', 'ยาง', 'อุบัติเหตุ']:

            cost_total = cost_fleet[metric].sum()

            if metric == 'PM':
                allocated_total = alloc_df['PM-หัว'].sum()
            else:
                allocated_total = alloc_df[f'{metric}-หัว'].sum() + \
                                  alloc_df[f'{metric}-หาง'].sum()

            summary_rows.append({
                'Fleet': fleet_name,
                'Metric': metric,
                'Cost Total': cost_total,
                'Allocated Total': allocated_total,
                'Diff': allocated_total - cost_total
            })

    recheck_df = pd.DataFrame(summary_rows)

    # =========================================================
    # EXPORT EXCEL
    # =========================================================

    output = BytesIO()

    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        tdm.to_excel(writer, sheet_name='tdm', index=False)
        trailer.to_excel(writer, sheet_name='Trailer', index=False)
        cost_with_fleet.to_excel(writer, sheet_name='cost_with_fleet', index=False)
        recheck_df.to_excel(writer, sheet_name='recheck_total', index=False)

    output.seek(0)

    return output