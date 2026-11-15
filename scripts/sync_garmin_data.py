"""
Garmin Connect Personal API — Complete Lifetime Data Sync Engine
================================================================
Synchronizes the athlete's complete lifetime Garmin Connect history:
  - All lifetime activities (2013-present) with original FIT binaries and summary index.
  - All available daily wellness telemetry (2019-present): daily user summaries,
    nocturnal HRV rMSSD, sleep stages, body battery, stress, steps, and weight.

Features:
  - Automatic rate-limit handling (HTTP 429 retry-after backoff).
  - Idempotent and resumable (skips existing non-empty FIT and JSON files).
  - Clean date-chunking across multi-year spans.

Usage:
    python scripts/sync_garmin_data.py --all
    python scripts/sync_garmin_data.py --days 365
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

from garmin_personal_api import GarminPersonalApiClient, PersonalApiAuthenticator
from garmin_personal_api.errors import HttpError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("garmin_sync")

T = TypeVar("T")


def load_garmin_key(env_path: Path) -> str:
    """Loads and extracts the Garmin Personal API key from a .env file or raw key file."""
    if not env_path.is_file():
        raise FileNotFoundError(f"Garmin environment file not found at: {env_path}")

    content = env_path.read_text(encoding="utf-8").strip()
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            v = v.strip().strip("'\"")
            if "KEY" in k.upper() or "TOKEN" in k.upper() or v.startswith("GCDC_"):
                return v
        elif line.startswith("GCDC_"):
            return line

    first_line = content.splitlines()[0].strip()
    if first_line:
        return first_line

    raise ValueError(f"No valid Garmin API Key found in {env_path}")


def call_with_retry(fn: Callable[[], T], max_retries: int = 10, desc: str = "") -> T:
    """Executes a callable with automatic retry on rate limiting (HTTP 429) or transient server errors."""
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except HttpError as e:
            if e.status_code == 429:
                wait_sec = 35.0
                try:
                    data = json.loads(e.response_text)
                    wait_sec = float(data.get("retryInSeconds", 35))
                except Exception:
                    pass
                wait_total = wait_sec + 2.0
                logger.warning(
                    f"Rate limited (HTTP 429) on {desc}. Garmin requested wait of {wait_sec:.0f}s. "
                    f"Pausing for {wait_total:.0f}s before retry (attempt {attempt}/{max_retries})..."
                )
                time.sleep(wait_total)
            elif e.status_code in (500, 502, 503, 504) and attempt < max_retries:
                logger.warning(f"Server error {e.status_code} on {desc}. Retrying in 5s (attempt {attempt}/{max_retries})...")
                time.sleep(5.0)
            else:
                raise
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"Transient error on {desc}: {e}. Retrying in 3s (attempt {attempt}/{max_retries})...")
                time.sleep(3.0)
            else:
                raise

    raise RuntimeError(f"Max retries exceeded for {desc}")


def chunk_date_ranges(start: date, end: date, max_days: int = 28) -> List[Tuple[date, date]]:
    """Splits a [start, end] date interval into chunks of at most max_days."""
    chunks = []
    curr = start
    while curr <= end:
        chunk_end = min(curr + timedelta(days=max_days - 1), end)
        chunks.append((curr, chunk_end))
        curr = chunk_end + timedelta(days=1)
    return chunks


def chunk_years(start: date, end: date) -> List[Tuple[date, date]]:
    """Splits a [start, end] date interval into calendar year chunks (max 366 days)."""
    chunks = []
    curr_year = start.year
    while curr_year <= end.year:
        chunk_start = start if curr_year == start.year else date(curr_year, 1, 1)
        chunk_end = end if curr_year == end.year else date(curr_year, 12, 31)
        chunks.append((chunk_start, chunk_end))
        curr_year += 1
    return chunks


def fetch_wellness_telemetry(
    client: GarminPersonalApiClient,
    start_date: date,
    end_date: date,
    wellness_dir: Path,
    redownload: bool = False,
) -> Dict[str, Any]:
    """Pulls down all daily health, sleep, stress, body battery, steps, and weight telemetry."""
    wellness_dir.mkdir(parents=True, exist_ok=True)
    summary_report: Dict[str, Any] = {}

    s_str = start_date.isoformat()
    e_str = end_date.isoformat()
    logger.info(f"Synchronizing wellness telemetry from {s_str} to {e_str}...")

    # 1. User Profile Settings
    try:
        profile = call_with_retry(lambda: client.get_user_profile_settings(), desc="user_profile")
        (wellness_dir / "user_profile.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")
        logger.info("  [✓] User profile settings saved.")
        summary_report["user_profile"] = "saved"
    except Exception as e:
        logger.warning(f"  [!] Failed to fetch user profile: {e}")
        summary_report["user_profile"] = f"error: {e}"

    # 2. Heart Rate Zones
    try:
        hr_zones = call_with_retry(lambda: client.get_heart_rate_zones(), desc="heart_rate_zones")
        (wellness_dir / "heart_rate_zones.json").write_text(json.dumps(hr_zones, indent=2), encoding="utf-8")
        logger.info(f"  [✓] Heart rate zones saved ({len(hr_zones)} sport configs).")
        summary_report["heart_rate_zones"] = len(hr_zones)
    except Exception as e:
        logger.warning(f"  [!] Failed to fetch heart rate zones: {e}")
        summary_report["heart_rate_zones"] = f"error: {e}"

    # 3. Fitness Age
    try:
        fitness_age = call_with_retry(lambda: client.get_fitness_age(e_str), desc="fitness_age")
        (wellness_dir / "fitness_age.json").write_text(json.dumps(fitness_age, indent=2), encoding="utf-8")
        logger.info("  [✓] Fitness age saved.")
        summary_report["fitness_age"] = fitness_age.get("fitnessAge")
    except Exception as e:
        logger.warning(f"  [!] Failed to fetch fitness age: {e}")
        summary_report["fitness_age"] = f"error: {e}"

    # 4. Effective Weight Goals
    try:
        weight_goals = call_with_retry(lambda: client.get_effective_weight_goals(s_str, e_str), desc="weight_goals")
        (wellness_dir / "weight_goals.json").write_text(json.dumps(weight_goals, indent=2), encoding="utf-8")
        logger.info("  [✓] Weight goals saved.")
    except Exception as e:
        logger.debug(f"Weight goals fetch: {e}")

    # Year Chunks for 1-year endpoints
    year_chunks = chunk_years(start_date, end_date)
    logger.info(f"Querying annual wellness endpoints across {len(year_chunks)} year intervals...")

    # 5. Daily User Summaries (multi-year)
    daily_file = wellness_dir / "daily_summaries.json"
    if not daily_file.exists() or redownload:
        all_daily_records: List[Dict[str, Any]] = []
        for y_start, y_end in year_chunks:
            try:
                ds = call_with_retry(
                    lambda: client.get_daily_summaries(y_start.isoformat(), y_end.isoformat()),
                    desc=f"daily_summaries_{y_start.year}",
                )
                items = ds.get("data", []) if isinstance(ds, dict) else []
                # Keep days with actual telemetry
                for r in items:
                    has_data = (
                        (r.get("movement") or {}).get("steps", {}).get("value") is not None
                        or (r.get("heartRate") or {}).get("restingValue") is not None
                        or r.get("calories", {}).get("totalValue") is not None
                    )
                    if has_data:
                        all_daily_records.append(r)
                time.sleep(0.1)
            except Exception as e:
                logger.warning(f"Daily summaries for {y_start.year} failed: {e}")

        # Deduplicate and sort chronologically
        deduped_daily = {r.get("calendarDate"): r for r in all_daily_records if r.get("calendarDate")}
        sorted_daily = [deduped_daily[k] for k in sorted(deduped_daily.keys())]
        daily_file.write_text(json.dumps({"data": sorted_daily}, indent=2), encoding="utf-8")
        logger.info(f"  [✓] Daily user summaries saved ({len(sorted_daily)} active days).")
        summary_report["daily_summaries_days"] = len(sorted_daily)
    else:
        cached_count = len(json.loads(daily_file.read_text(encoding="utf-8")).get("data", []))
        summary_report["daily_summaries_days"] = cached_count
        logger.info(f"  [✓] Daily user summaries loaded from cache ({cached_count} days).")

    # 6. HRV Daily Summaries (multi-year)
    hrv_file = wellness_dir / "hrv_summaries.json"
    if not hrv_file.exists() or redownload:
        all_hrv_records: List[Dict[str, Any]] = []
        for y_start, y_end in year_chunks:
            try:
                hrv = call_with_retry(
                    lambda: client.get_daily_hrv_summaries(y_start.isoformat(), y_end.isoformat()),
                    desc=f"hrv_summaries_{y_start.year}",
                )
                items = hrv.get("hrvSummaries", []) if isinstance(hrv, dict) else []
                all_hrv_records.extend(items)
                time.sleep(0.1)
            except Exception as e:
                logger.warning(f"HRV summaries for {y_start.year} failed: {e}")

        deduped_hrv = {r.get("calendarDate"): r for r in all_hrv_records if r.get("calendarDate")}
        sorted_hrv = [deduped_hrv[k] for k in sorted(deduped_hrv.keys())]
        hrv_file.write_text(json.dumps({"hrvSummaries": sorted_hrv}, indent=2), encoding="utf-8")
        logger.info(f"  [✓] HRV summaries saved ({len(sorted_hrv)} days with nocturnal rMSSD).")
        summary_report["hrv_days"] = len(sorted_hrv)
    else:
        cached_count = len(json.loads(hrv_file.read_text(encoding="utf-8")).get("hrvSummaries", []))
        summary_report["hrv_days"] = cached_count
        logger.info(f"  [✓] HRV summaries loaded from cache ({cached_count} days).")

    # 7. Weight Measurements (multi-year)
    weight_file = wellness_dir / "weight_measurements.json"
    if not weight_file.exists() or redownload:
        all_weights: List[Dict[str, Any]] = []
        for y_start, y_end in year_chunks:
            try:
                w = call_with_retry(
                    lambda: client.get_weight_measurements(y_start.isoformat(), y_end.isoformat()),
                    desc=f"weight_{y_start.year}",
                )
                items = w.get("dailyWeightSummaries", []) if isinstance(w, dict) else []
                all_weights.extend(items)
                time.sleep(0.1)
            except Exception as e:
                logger.warning(f"Weight for {y_start.year} failed: {e}")

        deduped_w = {
            r.get("summaryDate") or r.get("calendarDate") or r.get("date"): r
            for r in all_weights
            if r.get("summaryDate") or r.get("calendarDate") or r.get("date")
        }
        sorted_w = [deduped_w[k] for k in sorted(deduped_w.keys())]
        weight_file.write_text(json.dumps({"dailyWeightSummaries": sorted_w}, indent=2), encoding="utf-8")
        logger.info(f"  [✓] Weight measurements saved ({len(sorted_w)} records).")
        summary_report["weight_records"] = len(sorted_w)
    else:
        cached_count = len(json.loads(weight_file.read_text(encoding="utf-8")).get("dailyWeightSummaries", []))
        summary_report["weight_records"] = cached_count
        logger.info(f"  [✓] Weight measurements loaded from cache ({cached_count} records).")

    # 28-day Chunked Endpoints
    month_chunks = chunk_date_ranges(start_date, end_date, max_days=28)
    logger.info(f"Querying chunked endpoints across {len(month_chunks)} 28-day intervals...")

    # 8. Sleep Summaries
    sleep_file = wellness_dir / "sleep_summaries.json"
    if not sleep_file.exists() or redownload:
        all_sleep: List[Dict[str, Any]] = []
        for idx, (c_start, c_end) in enumerate(month_chunks, start=1):
            try:
                res = call_with_retry(
                    lambda: client.get_daily_sleep_summaries(c_start.isoformat(), c_end.isoformat()),
                    desc=f"sleep_{c_start}_{c_end}",
                )
                if isinstance(res, list):
                    for entry in res:
                        if entry.get("sleepScores") or entry.get("durationInSeconds"):
                            all_sleep.append(entry)
                time.sleep(0.08)
            except Exception as e:
                logger.warning(f"Sleep chunk {c_start} to {c_end} failed: {e}")
            if idx % 25 == 0 or idx == len(month_chunks):
                logger.info(f"    Sleep sync progress: {idx}/{len(month_chunks)} chunks...")

        deduped_sleep = {r.get("calendarDate"): r for r in all_sleep if r.get("calendarDate")}
        sorted_sleep = [deduped_sleep[k] for k in sorted(deduped_sleep.keys())]
        sleep_file.write_text(json.dumps(sorted_sleep, indent=2), encoding="utf-8")
        logger.info(f"  [✓] Sleep summaries saved ({len(sorted_sleep)} night records).")
        summary_report["sleep_records"] = len(sorted_sleep)
    else:
        cached_count = len(json.loads(sleep_file.read_text(encoding="utf-8")))
        summary_report["sleep_records"] = cached_count
        logger.info(f"  [✓] Sleep summaries loaded from cache ({cached_count} records).")

    # 9. Body Battery
    bb_file = wellness_dir / "body_battery_summaries.json"
    if not bb_file.exists() or redownload:
        all_bb: List[Dict[str, Any]] = []
        for idx, (c_start, c_end) in enumerate(month_chunks, start=1):
            try:
                res = call_with_retry(
                    lambda: client.get_daily_body_battery(c_start.isoformat(), c_end.isoformat()),
                    desc=f"body_battery_{c_start}_{c_end}",
                )
                if isinstance(res, list):
                    for entry in res:
                        if entry.get("charged") is not None or entry.get("drained") is not None:
                            all_bb.append(entry)
                time.sleep(0.08)
            except Exception as e:
                logger.warning(f"Body battery chunk {c_start} to {c_end} failed: {e}")
            if idx % 25 == 0 or idx == len(month_chunks):
                logger.info(f"    Body Battery sync progress: {idx}/{len(month_chunks)} chunks...")

        deduped_bb = {r.get("date"): r for r in all_bb if r.get("date")}
        sorted_bb = [deduped_bb[k] for k in sorted(deduped_bb.keys())]
        bb_file.write_text(json.dumps(sorted_bb, indent=2), encoding="utf-8")
        logger.info(f"  [✓] Body battery reports saved ({len(sorted_bb)} active days).")
        summary_report["body_battery_days"] = len(sorted_bb)
    else:
        cached_count = len(json.loads(bb_file.read_text(encoding="utf-8")))
        summary_report["body_battery_days"] = cached_count
        logger.info(f"  [✓] Body battery loaded from cache ({cached_count} days).")

    # 10. Stress Summaries
    stress_file = wellness_dir / "stress_summaries.json"
    if not stress_file.exists() or redownload:
        all_stress: List[Dict[str, Any]] = []
        for idx, (c_start, c_end) in enumerate(month_chunks, start=1):
            try:
                res = call_with_retry(
                    lambda: client.get_daily_stress(c_start.isoformat(), c_end.isoformat()),
                    desc=f"stress_{c_start}_{c_end}",
                )
                if isinstance(res, list):
                    for entry in res:
                        vals = entry.get("values") or {}
                        if vals.get("overallStressLevel") is not None or entry.get("overallStressLevel") is not None:
                            all_stress.append(entry)
                time.sleep(0.08)
            except Exception as e:
                logger.warning(f"Stress chunk {c_start} to {c_end} failed: {e}")
            if idx % 25 == 0 or idx == len(month_chunks):
                logger.info(f"    Stress sync progress: {idx}/{len(month_chunks)} chunks...")

        deduped_stress = {r.get("calendarDate"): r for r in all_stress if r.get("calendarDate")}
        sorted_stress = [deduped_stress[k] for k in sorted(deduped_stress.keys())]
        stress_file.write_text(json.dumps(sorted_stress, indent=2), encoding="utf-8")
        logger.info(f"  [✓] Stress summaries saved ({len(sorted_stress)} active days).")
        summary_report["stress_days"] = len(sorted_stress)
    else:
        cached_count = len(json.loads(stress_file.read_text(encoding="utf-8")))
        summary_report["stress_days"] = cached_count
        logger.info(f"  [✓] Stress summaries loaded from cache ({cached_count} days).")

    # 11. Steps Summaries
    steps_file = wellness_dir / "steps_summaries.json"
    if not steps_file.exists() or redownload:
        all_steps: List[Dict[str, Any]] = []
        for idx, (c_start, c_end) in enumerate(month_chunks, start=1):
            try:
                res = call_with_retry(
                    lambda: client.get_daily_steps(c_start.isoformat(), c_end.isoformat()),
                    desc=f"steps_{c_start}_{c_end}",
                )
                if isinstance(res, list):
                    for entry in res:
                        if entry.get("totalSteps") is not None and entry.get("totalSteps") > 0:
                            all_steps.append(entry)
                time.sleep(0.08)
            except Exception as e:
                logger.warning(f"Steps chunk {c_start} to {c_end} failed: {e}")
            if idx % 25 == 0 or idx == len(month_chunks):
                logger.info(f"    Steps sync progress: {idx}/{len(month_chunks)} chunks...")

        deduped_steps = {r.get("calendarDate"): r for r in all_steps if r.get("calendarDate")}
        sorted_steps = [deduped_steps[k] for k in sorted(deduped_steps.keys())]
        steps_file.write_text(json.dumps(sorted_steps, indent=2), encoding="utf-8")
        logger.info(f"  [✓] Step statistics saved ({len(sorted_steps)} active days).")
        summary_report["steps_days"] = len(sorted_steps)
    else:
        cached_count = len(json.loads(steps_file.read_text(encoding="utf-8")))
        summary_report["steps_days"] = cached_count
        logger.info(f"  [✓] Step statistics loaded from cache ({cached_count} days).")

    return summary_report


def fetch_activities_and_fit_files(
    client: GarminPersonalApiClient,
    start_date: Optional[date],
    end_date: date,
    activities_dir: Path,
    download_fit: bool = True,
    download_details: bool = False,
    redownload: bool = False,
) -> Dict[str, Any]:
    """Retrieves all activity summaries and downloads original raw FIT files with rate-limit resiliency."""
    fit_dir = activities_dir / "fit_files"
    details_dir = activities_dir / "details"
    activities_dir.mkdir(parents=True, exist_ok=True)
    if download_fit:
        fit_dir.mkdir(parents=True, exist_ok=True)
    if download_details:
        details_dir.mkdir(parents=True, exist_ok=True)

    summary_file = activities_dir / "activities_summary.json"
    matched_activities: List[Dict[str, Any]] = []

    # If full index already exists and not redownloading, reload
    if summary_file.is_file() and not redownload:
        try:
            cached = json.loads(summary_file.read_text(encoding="utf-8"))
            if len(cached) >= 2000 or (start_date and len(cached) >= 100):
                matched_activities = cached
                logger.info(f"Loaded {len(matched_activities)} activities from existing index.")
        except Exception:
            matched_activities = []

    if not matched_activities:
        start_iso = start_date.isoformat() if start_date else "2000-01-01"
        end_iso = end_date.isoformat()
        logger.info(f"Searching for all activities between {start_iso} and {end_iso}...")

        page_start = 0
        page_limit = 100

        while True:
            batch = call_with_retry(
                lambda: client.search_activities(start=page_start, limit=page_limit),
                desc=f"search_activities_offset_{page_start}",
            )
            if not batch or not isinstance(batch, list):
                break

            is_done = False
            for act in batch:
                start_local = act.get("startTimeLocal", "")[:10]
                if start_date and start_local < start_iso:
                    is_done = True
                    break
                if start_local <= end_iso:
                    matched_activities.append(act)

            if is_done or len(batch) < page_limit:
                break
            page_start += len(batch)
            logger.info(f"  Indexed {len(matched_activities)} activities so far (offset: {page_start})...")
            time.sleep(0.05)

        summary_file.write_text(
            json.dumps(matched_activities, indent=2), encoding="utf-8"
        )
        logger.info(f"Complete activity catalog saved: {len(matched_activities)} total activities indexed.")

    fit_downloaded = 0
    fit_skipped = 0
    fit_failed = 0
    details_downloaded = 0
    sport_counts: Dict[str, int] = {}

    total_total = len(matched_activities)
    logger.info(f"Downloading original binary FIT files for {total_total} activities...")

    for idx, act in enumerate(matched_activities, start=1):
        act_id = act.get("activityId")
        if not act_id:
            continue

        start_time_raw = act.get("startTimeLocal", "")
        date_str = start_time_raw[:10] if start_time_raw else "unknown_date"
        type_key = act.get("activityType", {}).get("typeKey", "activity")
        sport_counts[type_key] = sport_counts.get(type_key, 0) + 1

        clean_sport = "".join(c if c.isalnum() or c in "-_" else "_" for c in type_key)
        fit_filename = f"{date_str}_{clean_sport}_{act_id}.fit"
        fit_path = fit_dir / fit_filename
        detail_path = details_dir / f"{act_id}.json"

        # 1. Download Detailed JSON (if requested)
        if download_details:
            if detail_path.is_file() and detail_path.stat().st_size > 10 and not redownload:
                pass
            else:
                try:
                    detail = call_with_retry(
                        lambda: client.get_activity(act_id),
                        desc=f"activity_detail_{act_id}",
                    )
                    detail_path.write_text(json.dumps(detail, indent=2), encoding="utf-8")
                    details_downloaded += 1
                    time.sleep(0.1)
                except Exception as e:
                    logger.warning(f"Failed to fetch detail for activity {act_id}: {e}")

        # 2. Download FIT Binary File
        if download_fit:
            if fit_path.is_file() and fit_path.stat().st_size > 0 and not redownload:
                fit_skipped += 1
            else:
                try:
                    payload = call_with_retry(
                        lambda: client.download_activity(act_id),
                        desc=f"fit_download_{act_id}",
                    )
                    if not payload:
                        logger.warning(f"Empty payload for activity {act_id}")
                        fit_failed += 1
                        continue

                    # Garmin returns ZIP containing .fit file
                    if payload.startswith(b"PK\x03\x04"):
                        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                            fit_members = [m for m in zf.namelist() if m.lower().endswith(".fit")]
                            if fit_members:
                                fit_bytes = zf.read(fit_members[0])
                                fit_path.write_bytes(fit_bytes)
                                fit_downloaded += 1
                            else:
                                zf.extract(zf.namelist()[0], path=str(fit_dir))
                                fit_downloaded += 1
                    else:
                        fit_path.write_bytes(payload)
                        fit_downloaded += 1

                    time.sleep(0.12)  # Polite spacing to maximize throughput within rate limits
                except Exception as e:
                    logger.warning(f"Failed to download FIT for activity {act_id}: {e}")
                    fit_failed += 1

        if idx % 50 == 0 or idx == total_total:
            logger.info(
                f"  Activity progress: {idx}/{total_total} processed "
                f"({fit_downloaded} new FITs, {fit_skipped} already on disk, {fit_failed} failed)..."
            )

    return {
        "total_activities": total_total,
        "sport_breakdown": sport_counts,
        "fit_downloaded": fit_downloaded,
        "fit_skipped": fit_skipped,
        "fit_failed": fit_failed,
        "details_downloaded": details_downloaded,
    }


def main():
    parser = argparse.ArgumentParser(description="Synchronize complete data history from Garmin Connect Personal API")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Pull ALL historical data across the user's entire lifetime account history",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Number of days to look back if --all is not specified (default: 365)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/garmin.env",
        help="Path to garmin.env file (default: config/garmin.env)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/garmin",
        help="Base output directory (default: data/garmin)",
    )
    parser.add_argument(
        "--skip-fit",
        action="store_true",
        help="Skip downloading binary FIT files",
    )
    parser.add_argument(
        "--skip-activities",
        action="store_true",
        help="Skip activity sync entirely",
    )
    parser.add_argument(
        "--skip-wellness",
        action="store_true",
        help="Skip downloading daily wellness metrics",
    )
    parser.add_argument(
        "--download-details",
        action="store_true",
        help="Download individual activity JSON details (note: duplicates FIT contents and takes extra calls)",
    )
    parser.add_argument(
        "--redownload",
        action="store_true",
        help="Redownload files even if they already exist on disk",
    )

    args = parser.parse_args()

    env_path = Path(args.config)
    output_dir = Path(args.output_dir)

    logger.info(f"Loading Garmin credentials from {env_path}...")
    api_key = load_garmin_key(env_path)

    logger.info("Authenticating with Garmin Connect Personal API...")
    auth = PersonalApiAuthenticator()
    token = auth.obtain_token(api_key)
    logger.info("Authentication successful. Bearer token obtained.")

    client = GarminPersonalApiClient(token.access_token)

    end_date = date.today()
    if args.all:
        # User account was created in 2013; earliest activity is 2013-05-22.
        # Continuous wearable telemetry began 2019-08-31.
        act_start_date = None
        wellness_start_date = date(2019, 8, 1)
        logger.info(f"Target sync mode: COMPLETE LIFETIME HISTORY (Activities: 2013 -> {end_date.year}, Wellness: 2019 -> {end_date.year})")
    else:
        act_start_date = end_date - timedelta(days=args.days)
        wellness_start_date = act_start_date
        logger.info(f"Target sync window: {act_start_date.isoformat()} to {end_date.isoformat()} ({args.days} days)")

    # Execute Wellness Sync
    wellness_report = {}
    if not args.skip_wellness:
        wellness_report = fetch_wellness_telemetry(
            client, wellness_start_date, end_date, output_dir / "wellness", redownload=args.redownload
        )

    # Execute Activity & FIT Sync
    activity_report = {}
    if not args.skip_activities:
        activity_report = fetch_activities_and_fit_files(
            client,
            act_start_date,
            end_date,
            output_dir / "activities",
            download_fit=not args.skip_fit,
            download_details=args.download_details,
            redownload=args.redownload,
        )

    client.close()
    auth.close()

    print("\n" + "=" * 65)
    print("        GARMIN CONNECT LIFETIME DATA SYNC COMPLETED")
    print("=" * 65)
    print(f"Activities Range:    2013-05-22 -> {end_date.isoformat()}")
    print(f"Wellness Range:      {wellness_start_date.isoformat()} -> {end_date.isoformat()}")
    print(f"Destination:         {output_dir.resolve()}")
    print("-" * 65)
    print("WELLNESS METRICS:")
    for k, v in wellness_report.items():
        print(f"  • {k:22}: {v}")
    print("-" * 65)
    print("ACTIVITIES:")
    print(f"  • Total Activities:   {activity_report.get('total_activities', 0)}")
    print(f"  • FIT Downloaded (new):{activity_report.get('fit_downloaded', 0)}")
    print(f"  • FIT Pre-existing:   {activity_report.get('fit_skipped', 0)}")
    print(f"  • FIT Failed:         {activity_report.get('fit_failed', 0)}")
    if args.download_details:
        print(f"  • Details Downloaded: {activity_report.get('details_downloaded', 0)}")
    print("  • Sport Breakdown:")
    for sport, count in sorted(activity_report.get("sport_breakdown", {}).items(), key=lambda x: -x[1]):
        print(f"      - {sport:18}: {count}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
