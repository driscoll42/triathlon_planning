import os
import re
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
import numpy as np
import pandas as pd
from classes import triVariables as triv

# Raw-scrape normalization constants (TASK-013). NOT athlete thresholds: the xlsx scrape cache stores
# minutes/TSS computed with fixed reference values, and the canonical library (library.build)
# re-derives every athlete-dependent number from config/athlete_profile.yaml.
SCRAPE_REFERENCE_RUN_CP = 295.0
SCRAPE_REFERENCE_BIKE_CP = 170.0
SCRAPE_REFERENCE_SWIM_CSS = 35.29


def calculate_TSS(zone_times, zone_multipliers, ftp):
    """Calculates the training stress of the activity.
    This is essentially a power-based version of Banister's heart rate-based
    TRIMP (training impulse).
    """
    total_time = np.sum(zone_times) / 60.0
    if total_time == 0:
        return 0.0, 0.0, 0.0
    norm_power = np.sqrt(np.sqrt(np.sum(np.multiply((ftp * zone_multipliers) ** 4, zone_times)) / np.sum(zone_times)))
    intensity = norm_power / float(ftp)
    tss = 100 * (intensity ** 2) * total_time

    return norm_power, intensity, tss


def calculate_sTSS(zone_distances):
    """Calculates swimming TSS score based on CSS and distances."""
    total_distance = sum(zone_distances)
    if total_distance == 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    swim_speeds = triv.swim_zone_css_multiplier * SCRAPE_REFERENCE_SWIM_CSS
    zone_times = zone_distances / swim_speeds
    low_time = zone_times[0] + zone_times[1]
    high_time = zone_times[2] + zone_times[3] + zone_times[4]
    normalized_swim_speed = total_distance / sum(zone_times)
    intensity = normalized_swim_speed / SCRAPE_REFERENCE_SWIM_CSS
    stss = np.power(intensity, 3) * sum(zone_times) / 60.0 * 100

    return normalized_swim_speed, intensity, stss, low_time, high_time


class LibraryHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.fit_urls = set()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.current_cell = []
        self.current_row = []
        self.current_table = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == 'a' and 'href' in attrs_dict:
            href = attrs_dict['href']
            if '.fit' in href.lower():
                self.fit_urls.add(href)
        if tag == 'table':
            self.in_table = True
            self.current_table = []
        elif tag == 'tr' and self.in_table:
            self.in_row = True
            self.current_row = []
        elif tag in ('td', 'th') and self.in_row:
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.in_cell:
            self.in_cell = False
            self.current_row.append("".join(self.current_cell).strip())
        elif tag == 'tr' and self.in_row:
            self.in_row = False
            if self.current_row:
                self.current_table.append(self.current_row)
        elif tag == 'table' and self.in_table:
            self.in_table = False
            if self.current_table:
                self.tables.append(self.current_table)

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell.append(data)


def fetch_workout_library(url='https://www.8020endurance.com/8020-workout-library/', cache_file='data/workout_library.html'):
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        cache_file,
        os.path.join(root_dir, cache_file),
        'workout_library.html',
        os.path.join(root_dir, 'workout_library.html'),
    ]
    resolved = next((c for c in candidates if os.path.exists(c) and os.path.getsize(c) > 0), None)
    if resolved:
        print(f"Reading cached workout library from {resolved}...")
        with open(resolved, 'r', encoding='utf-8') as f:
            return f.read()

    print(f"Fetching workout library from {url}...")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode('utf-8')

    target_cache = os.path.join(root_dir, cache_file) if not os.path.isabs(cache_file) else cache_file
    os.makedirs(os.path.dirname(target_cache) or '.', exist_ok=True)
    with open(target_cache, 'w', encoding='utf-8') as f:
        f.write(html)
    return html


def download_fit_files(fit_urls, base_url='https://www.8020endurance.com', base_dir='data/fit_files'):
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        base_dir,
        os.path.join(root_dir, base_dir),
        'fit_files',
        os.path.join(root_dir, 'fit_files'),
    ]
    resolved = next((c for c in candidates if os.path.exists(c)), os.path.join(root_dir, 'data', 'fit_files'))
    os.makedirs(resolved, exist_ok=True)
    base_dir = resolved

    def _download_one(rel_url):
        full_url = urllib.parse.urljoin(base_url, rel_url)
        path = urllib.parse.urlparse(full_url).path
        parts = [p for p in path.split('/') if p]
        category = parts[-2] if len(parts) >= 2 else 'other'
        filename = urllib.parse.unquote(parts[-1])
        target_dir = os.path.join(base_dir, category)
        os.makedirs(target_dir, exist_ok=True)
        target_path = os.path.join(target_dir, filename)

        if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
            return target_path, True

        req = urllib.request.Request(full_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            content = resp.read()
            with open(target_path, 'wb') as f:
                f.write(content)
        return target_path, True

    print(f"Checking/downloading {len(fit_urls)} .FIT files into '{base_dir}'...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_download_one, u) for u in fit_urls]
        for f in as_completed(futures):
            f.result()
    print("All .FIT files downloaded successfully.")


def create_workout_dataframe(raw_rows, wkt_type):
    data = []
    if wkt_type in ('Run', 'Bike'):
        for row in raw_rows:
            data.append({
                'Code': row[0],
                'Duration': row[1],
                'Description': row[2]
            })
    elif wkt_type == 'Swim':
        for row in raw_rows:
            data.append({
                'Code': row[0],
                'Distance': float(row[1]) if row[1].isdigit() else row[1],
                'Description': row[-1]
            })

    df = pd.DataFrame(data)
    df[triv.type] = wkt_type

    for col in [triv.zone_1, triv.zone_2, triv.zone_3, triv.zone_4, triv.zone_5,
                triv.tss, triv.high_duration_head, triv.low_duration_head,
                triv.intensity_factor, triv.normalized_power]:
        df[col] = 0.0

    swim_speeds = triv.swim_zone_css_multiplier * SCRAPE_REFERENCE_SWIM_CSS

    for index, row in df.iterrows():
        descrip = row['Description']

        # Normalize descriptions (e.g. handle comma in intervals like "4 x (500 Zone 2, 60\" rest)")
        descrip = re.sub(r'(\d+\s+Zone\s+\d+),\s*(\d+[\"\s\w]*rest)', r'\1/\2', descrip)
        descrip = re.sub(r'\s*/\s*', '/', descrip)

        descrip_text = re.split(r',|[)]', descrip)

        has_mile = False
        for period in descrip_text:
            period = period.replace('Minute', 'minute')
            period = period.replace('minute ', 'minutes ')
            period = period.replace(' uphill or simulated', '')
            period = period.replace('uphill', '')
            period = period.replace(' in', '')
            period = period.replace('"', ' seconds')
            period = period.strip()

            if len(period) == 0:
                continue
            if 'mile' in period:
                has_mile = True
                continue
            elif 'x' in period:
                split_text = re.split(r' x [\(]', period)
                multiplier = float(split_text[0].strip())
                zone_split = re.split(r'/', split_text[1])

                if 'minutes' in zone_split[0]:
                    high = re.split('minutes', zone_split[0])
                    high_duration = float(high[0].strip())
                    high_zone = high[1].strip()
                elif 'seconds' in zone_split[0]:
                    high = re.split('seconds', zone_split[0])
                    high_duration = float(high[0].strip()) / 60.0
                    high_zone = high[1].strip()
                else:
                    high = re.split(r'\s+', zone_split[0].strip(), maxsplit=1)
                    high_duration = float(high[0].strip())
                    high_zone = high[1].strip()

                if len(zone_split) > 1 and 'rest' in zone_split[1]:
                    low_duration = 0.0
                    low_zone = 'Zone 1'
                elif len(zone_split) > 1 and 'minutes' in zone_split[1]:
                    low = re.split('minutes', zone_split[1])
                    low_duration = float(low[0].strip())
                    low_zone = low[1].strip()
                elif len(zone_split) > 1 and 'seconds' in zone_split[1]:
                    low = re.split('seconds', zone_split[1])
                    low_duration = float(low[0].strip()) / 60.0
                    low_zone = low[1].strip()
                elif len(zone_split) > 1:
                    low = re.split(r'\s+', zone_split[1].strip(), maxsplit=1)
                    low_duration = float(low[0].strip())
                    low_zone = low[1].strip()
                else:
                    low_duration = 0.0
                    low_zone = 'Zone 1'

                df.loc[index, low_zone] += multiplier * low_duration
                df.loc[index, high_zone] += multiplier * high_duration
                df.loc[index, triv.low_duration_head] += multiplier * low_duration
                if high_zone == triv.zone_2:
                    df.loc[index, triv.low_duration_head] += multiplier * high_duration
                else:
                    df.loc[index, triv.high_duration_head] += multiplier * high_duration

            elif 'rest' in period:
                pass
            else:
                if 'minutes' in period:
                    split_text = re.split('minutes', period)
                elif 'seconds' in period:
                    split_text = re.split('seconds', period)
                    split_text = [str(float(split_text[0].strip()) / 60.0), split_text[1]]
                else:
                    split_text = re.split(r'\s+', period.strip(), maxsplit=1)

                zone_time = float(split_text[0].strip())
                zone = split_text[1].strip()
                df.loc[index, zone] += zone_time
                if zone in (triv.zone_1, triv.zone_2):
                    df.loc[index, triv.low_duration_head] += zone_time
                else:
                    df.loc[index, triv.high_duration_head] += zone_time

        if has_mile:
            # Mile-prescribed runs stay TSS 0 in the raw scrape cache; library.build converts them to
            # distance-prescribed canonical rows materialized from the athlete's easy pace (TASK-012).
            df.loc[index, triv.tss] = 0.0
        else:
            z1 = float(df.loc[index, triv.zone_1])
            z2 = float(df.loc[index, triv.zone_2])
            z3 = float(df.loc[index, triv.zone_3])
            z4 = float(df.loc[index, triv.zone_4])
            z5 = float(df.loc[index, triv.zone_5])
            zone_vals = np.array([z1, z2, z3, z4, z5])

            if wkt_type == 'Run':
                norm_p, int_f, tss_val = calculate_TSS(zone_vals, triv.run_zone_power_multiplier, SCRAPE_REFERENCE_RUN_CP)
                df.loc[index, triv.normalized_power] = norm_p
                df.loc[index, triv.intensity_factor] = int_f
                df.loc[index, triv.tss] = tss_val
            elif wkt_type == 'Bike':
                norm_p, int_f, tss_val = calculate_TSS(zone_vals, triv.bike_zone_power_multiplier, SCRAPE_REFERENCE_BIKE_CP)
                df.loc[index, triv.normalized_power] = norm_p
                df.loc[index, triv.intensity_factor] = int_f
                df.loc[index, triv.tss] = tss_val
            elif wkt_type == 'Swim':
                norm_p, int_f, tss_val, low_time, high_time = calculate_sTSS(zone_vals)
                # For swim, convert zone columns from distance to minutes
                zone_times = zone_vals / swim_speeds
                df.loc[index, triv.zone_1] = zone_times[0]
                df.loc[index, triv.zone_2] = zone_times[1]
                df.loc[index, triv.zone_3] = zone_times[2]
                df.loc[index, triv.zone_4] = zone_times[3]
                df.loc[index, triv.zone_5] = zone_times[4]

                df.loc[index, triv.high_duration_head] = high_time
                df.loc[index, triv.low_duration_head] = low_time
                df.loc[index, 'Duration'] = low_time + high_time
                df.loc[index, triv.normalized_power] = norm_p
                df.loc[index, triv.intensity_factor] = int_f
                df.loc[index, triv.tss] = tss_val

    # Ensure Duration is numeric
    if 'Duration' in df.columns:
        df['Duration'] = pd.to_numeric(df['Duration'], errors='coerce').fillna(0.0)

    return df


def _is_custom_code(code) -> bool:
    """Custom (non-80/20-library) rows use underscore codes, e.g. RUN_SS1_STR, BK_TH_OU1, SW_CSS_TH1."""
    return '_' in str(code)


def preserve_custom_rows(new_df: pd.DataFrame, path: str) -> pd.DataFrame:
    """Re-append rows from the existing file that the scrape does not produce (TASK-006).

    Rebuilding from the 80/20 HTML library would otherwise silently drop the hand/agent-authored
    custom rows (provenance per ADR-012 / TASK-028). Scraped rows win on code collisions.
    """
    if not path or not os.path.exists(path):
        return new_df
    old_df = pd.read_excel(path, engine='openpyxl')
    old_df = old_df.loc[:, ~old_df.columns.astype(str).str.startswith('Unnamed')]
    scraped_codes = set(new_df[triv.code].astype(str))
    keep = old_df[old_df[triv.code].astype(str).map(lambda c: _is_custom_code(c) and c not in scraped_codes)]
    if keep.empty:
        return new_df
    print(f"Preserving {len(keep)} custom rows from {path}: {list(keep[triv.code])}")
    return pd.concat([new_df, keep], ignore_index=True)


def rebuild_all_files():
    html = fetch_workout_library()

    parser = LibraryHTMLParser()
    parser.feed(html)

    # Download all FIT files
    download_fit_files(parser.fit_urls)

    # Tables 1 to 17: Run (190 rows)
    run_rows = [row for t in parser.tables[1:18] for row in t[1:]]
    run_df = create_workout_dataframe(run_rows, 'Run')
    run_df = preserve_custom_rows(run_df, triv.run_file)
    run_df.to_excel(triv.run_file, engine='openpyxl')
    print(f"Created {triv.run_file} with {len(run_df)} workouts.")

    # Tables 18 to 27: Bike (218 rows)
    bike_rows = [row for t in parser.tables[18:28] for row in t[1:]]
    bike_df = create_workout_dataframe(bike_rows, 'Bike')
    bike_df = preserve_custom_rows(bike_df, triv.bike_file)
    bike_df.to_excel(triv.bike_file, engine='openpyxl')
    print(f"Created {triv.bike_file} with {len(bike_df)} workouts.")

    # Tables 28 to 36: Swim (71 rows)
    swim_rows = [row for t in parser.tables[28:37] for row in t[1:]]
    swim_df = create_workout_dataframe(swim_rows, 'Swim')
    swim_df = preserve_custom_rows(swim_df, triv.swim_file)
    swim_df.to_excel(triv.swim_file, engine='openpyxl')
    print(f"Created {triv.swim_file} with {len(swim_df)} workouts.")

    # The xlsx files are the raw scrape cache. Push the fresh scrape into the canonical library,
    # re-deriving 80/20 rows (including mile-prescribed long runs, TASK-012) and keeping every
    # authored row untouched (ADR-014).
    from library.build import refresh_from_8020_scrape
    from library.loader import LIBRARY_PATH, load_library
    canonical = refresh_from_8020_scrape(load_library(profile=None), triv.run_file, triv.bike_file, triv.swim_file)
    canonical.to_csv(LIBRARY_PATH, index=False)
    print(f"Refreshed {LIBRARY_PATH} ({len(canonical)} workouts).")

    return run_df, bike_df, swim_df


if __name__ == '__main__':
    rebuild_all_files()
