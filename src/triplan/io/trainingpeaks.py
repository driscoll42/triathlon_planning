#!/usr/bin/env python3
"""
sync_8020_to_tp.py - Sync 80/20 Running Workouts to TrainingPeaks Workout Library

Scrapes missing running categories from 80/20 Endurance Workout Library
and batch-uploads them as structured running workouts into TrainingPeaks via
the private REST API.
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from triplan.paths import CONFIG_DIR, DATA_DIR

# Optional tqdm for progress bar
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sync_8020_to_tp")

# Credentials live only in config/ under the repo root (CLAUDE.md credential hygiene; TASK-031).
for env_path in [
    str(CONFIG_DIR / ".env"),
    str(CONFIG_DIR / "trainingpeak.env"),
]:
    if os.path.exists(env_path):
        load_dotenv(env_path)

# Target 80/20 running categories
TARGET_CATEGORIES: Dict[str, str] = {
    "RCI": "Run Cruise Intervals",
    "RFF": "Run Fast Finish",
    "RHR": "Run Hill Repeats",
    "RL": "Run Long",
    "RLFF": "Run Long Fast Finish",
    "RLI": "Run Long Intervals",
    "RMI": "Run Mixed Intervals",
    "RSP": "Run Speed Play",
    "RT": "Run Tempo",
}

# 8020 Running Power ranges (% of rFTP / Running FTP)
ZONE_POWER_RANGES: Dict[int, Tuple[int, int]] = {
    1: (50, 76),
    2: (76, 88),
    3: (94, 100),
    4: (103, 120),
    5: (120, 150),
}

# 80/20 Running pace ranges (% of Threshold Pace)
ZONE_PACE_RANGES: Dict[int, Tuple[int, int]] = {
    1: (73, 83),
    2: (84, 91),
    3: (92, 99),
    4: (100, 104),
    5: (105, 111),
}

# 80/20 Power/Pace multipliers for TSS/IF estimation
ZONE_POWER_MULTIPLIERS: Dict[int, float] = {
    1: 0.63,
    2: 0.82,
    3: 0.97,
    4: 1.115,
    5: 1.35,
}

SOURCE_URL = "https://www.8020endurance.com/8020-workout-library/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ============================================================================
# 1. Scraping & Parsing (fetch_8020_workouts)
# ============================================================================

def clean_description_text(desc: str) -> str:
    """Normalize HTML artifacts, spacing, and punctuation in description."""
    if not desc:
        return ""
    # Strip HTML tags
    desc = re.sub(r"<[^>]+>", " ", desc)
    # Ensure space between 'Zone' and digit (e.g. 'Zone3' -> 'Zone 3')
    desc = re.sub(r"Zone(\d)", r"Zone \1", desc, flags=re.I)
    # Normalize slashes
    desc = re.sub(r"\s*/\s*", " / ", desc)
    # Collapse multiple whitespaces
    desc = re.sub(r"\s+", " ", desc).strip()
    # Ensure comma after closing parenthesis if followed by next item
    desc = re.sub(r"\)\s*(?=[a-zA-Z0-9])", "), ", desc)
    return desc


def fetch_8020_workouts(
    url: str = SOURCE_URL,
    html_content: Optional[str] = None,
    target_categories: Optional[Dict[str, str]] = None,
    local_cache_file: Optional[str] = "workout_library.html",
) -> List[Dict[str, Any]]:
    """
    Fetch the 80/20 Workout Library page, filter rows for target categories,
    and extract workout metadata.

    Returns a list of dictionaries with keys:
      - 'code': Normalized workout code (e.g., 'RCI01')
      - 'title': Human-readable workout title (e.g., 'Run Cruise Intervals 01')
      - 'total_duration': Duration in seconds (None if distance-based)
      - 'total_distance': Distance in meters (None if duration-based)
      - 'description': Cleaned workout description
      - 'category': Category code (e.g., 'RCI')
      - 'category_name': Category full name (e.g., 'Run Cruise Intervals')
      - 'raw_code': Raw code from table (e.g., 'RCI1')
      - 'raw_duration': Raw duration string from table (e.g., '60' or '6 mi')
    """
    if target_categories is None:
        target_categories = TARGET_CATEGORIES

    if not html_content:
        # Check local cache first or fetch from web
        fetched = False
        try:
            logger.info("Fetching workout library from %s ...", url)
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            if resp.status_code == 200:
                html_content = resp.text
                fetched = True
                # Cache response
                if local_cache_file:
                    try:
                        with open(local_cache_file, "w", encoding="utf-8") as f:
                            f.write(html_content)
                    except OSError:
                        pass
        except Exception as e:
            logger.warning("Could not fetch from URL (%s). Checking local cache...", e)

        if not fetched and local_cache_file and os.path.exists(local_cache_file):
            logger.info("Loading workout library from local file: %s", local_cache_file)
            with open(local_cache_file, "r", encoding="utf-8") as f:
                html_content = f.read()

        if not html_content:
            raise RuntimeError(
                f"Failed to fetch {url} and no local cache ({local_cache_file}) available."
            )

    soup = BeautifulSoup(html_content, "html.parser")
    workouts: List[Dict[str, Any]] = []

    # Sort category keys descending by length so RLFF/RLI/RMI are checked before RL
    sorted_cat_keys = sorted(target_categories.keys(), key=len, reverse=True)

    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        for row in rows[1:]:
            cells = [
                cell.get_text(separator=" ", strip=True)
                for cell in row.find_all(["td", "th"])
            ]
            if not cells:
                continue

            raw_code = cells[0].strip()

            matched_cat = None
            matched_num = None
            for cat in sorted_cat_keys:
                m = re.match(rf"^({re.escape(cat)})(\d+)$", raw_code, re.I)
                if m:
                    matched_cat = cat
                    matched_num = int(m.group(2))
                    break

            if not matched_cat:
                continue

            raw_duration = cells[1].strip() if len(cells) > 1 else ""
            raw_description = cells[2].strip() if len(cells) > 2 else ""
            cleaned_description = clean_description_text(raw_description)

            # Format code with zero-padding (e.g., RCI01, RL01, RFF05)
            normalized_code = f"{matched_cat}{matched_num:02d}"
            cat_name = target_categories[matched_cat]
            title = f"{cat_name} {matched_num:02d}"

            # Parse table duration/distance
            total_duration_sec: Optional[int] = None
            total_distance_m: Optional[int] = None

            if "mi" in raw_duration.lower():
                try:
                    miles_val = float(re.findall(r"[\d\.]+", raw_duration)[0])
                    total_distance_m = round(miles_val * 1609.344)
                except (IndexError, ValueError):
                    pass
            elif raw_duration:
                try:
                    minutes_val = float(re.findall(r"[\d\.]+", raw_duration)[0])
                    total_duration_sec = round(minutes_val * 60)
                except (IndexError, ValueError):
                    pass

            workouts.append({
                "code": normalized_code,
                "title": title,
                "total_duration": total_duration_sec,
                "total_distance": total_distance_m,
                "description": cleaned_description,
                "category": matched_cat,
                "category_name": cat_name,
                "raw_code": raw_code,
                "raw_duration": raw_duration,
            })

    # Sort workouts by category and code number
    def sort_key(w: Dict[str, Any]) -> Tuple[str, int]:
        m = re.search(r"(\d+)$", w["code"])
        return (w["category"], int(m.group(1)) if m else 0)

    workouts.sort(key=sort_key)
    logger.info("Scraped %d target running workouts across %d categories.", len(workouts), len(target_categories))
    return workouts


# ============================================================================
# 2. Structured Step Parser (parse_intervals_to_tp_structure)
# ============================================================================

def _parse_single_step(token: str, context: str = "active", metric: str = "power") -> Dict[str, Any]:
    """Parse an individual step string like '5 minutes Zone 1' or '30 seconds Zone 5 uphill'."""
    pattern = (
        r"^\s*([\d\.]+)\s*(minutes?|seconds?|miles?|min|sec|mi)\s+"
        r"Zone\s*(\d)(?:\s*uphill)?\s*$"
    )
    m = re.match(pattern, token.strip(), re.I)
    if not m:
        raise ValueError(f"Could not parse interval step: '{token}'")

    val = float(m.group(1))
    unit_str = m.group(2).lower()
    zone = int(m.group(3))

    if "mile" in unit_str or unit_str == "mi":
        unit = "meter"
        length_val = round(val * 1609.344)
    elif "sec" in unit_str:
        unit = "second"
        length_val = round(val)
    else:  # minutes
        unit = "second"
        length_val = round(val * 60)

    if metric == "power":
        min_target, max_target = ZONE_POWER_RANGES.get(zone, (50, 76))
    else:
        min_target, max_target = ZONE_PACE_RANGES.get(zone, (73, 83))

    if context == "warmUp":
        step_name = "Warm up"
        intensity_class = "warmUp"
    elif context == "coolDown":
        step_name = "Cool Down"
        intensity_class = "coolDown"
    elif context == "rest":
        step_name = "Easy"
        intensity_class = "rest"
    elif context == "recovery":
        step_name = "Recovery"
        intensity_class = "rest"
    elif context == "interval":
        step_name = "Hard"
        intensity_class = "active"
    else:
        step_name = "Active"
        intensity_class = "active"

    return {
        "name": step_name,
        "length": {"value": length_val, "unit": unit},
        "targets": [{"minValue": min_target, "maxValue": max_target}],
        "intensityClass": intensity_class,
        "openDuration": False,
        "_zone": zone,
    }


def _split_respecting_parentheses(text: str, delimiters: str = ",") -> List[str]:
    """Split text by delimiters, respecting balanced parentheses."""
    items: List[str] = []
    current: List[str] = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char in delimiters and depth == 0:
            part = "".join(current).strip()
            if part:
                items.append(part)
            current = []
        else:
            current.append(char)
    part = "".join(current).strip()
    if part:
        items.append(part)
    return items


def _parse_block(
    item_str: str,
    is_first: bool = False,
    is_last: bool = False,
    has_preceding_rep: bool = False,
    metric: str = "power",
) -> Dict[str, Any]:
    """Recursively parse a step token or a repeat loop like '3 x (5 min Zone 3 / 3 min Zone 1)'."""
    repeat_match = re.match(r"^(\d+)\s*x\s*\((.*)\)$", item_str.strip(), re.I | re.DOTALL)
    if repeat_match:
        count = int(repeat_match.group(1))
        inner_content = repeat_match.group(2).strip()
        inner_tokens = _split_respecting_parentheses(inner_content, delimiters="/,")

        inner_steps: List[Dict[str, Any]] = []
        for idx, tok in enumerate(inner_tokens):
            if re.match(r"^\d+\s*x\s*\(", tok.strip(), re.I):
                # Nested repeat loop
                nested_block = _parse_block(tok, metric=metric)
                inner_steps.append(nested_block)
            else:
                m_zone = re.search(r"Zone\s*(\d)", tok, re.I)
                z = int(m_zone.group(1)) if m_zone else 1
                # In intervals, first is active interval, second is rest if Zone 1
                ctx = "rest" if (idx > 0 and z == 1) else "interval"
                step_obj = _parse_single_step(tok, context=ctx, metric=metric)
                step_obj["type"] = "step"
                inner_steps.append(step_obj)

        return {
            "type": "repetition",
            "length": {"value": count, "unit": "repetition"},
            "steps": inner_steps,
        }
    else:
        m_zone = re.search(r"Zone\s*(\d)", item_str, re.I)
        z = int(m_zone.group(1)) if m_zone else 1
        ctx = "active"
        if is_first and z == 1:
            ctx = "warmUp"
        elif is_last and z == 1:
            ctx = "coolDown"
        elif has_preceding_rep and z == 1:
            ctx = "recovery"

        step_obj = _parse_single_step(item_str, context=ctx, metric=metric)
        return {
            "type": "step",
            "length": {"value": 1, "unit": "repetition"},
            "steps": [step_obj],
        }


def _calculate_block_length(block: Dict[str, Any]) -> int:
    """Calculate the cumulative length (seconds or meters) of a block."""
    if block.get("type") == "repetition":
        rep_count = block["length"]["value"]
        inner_sum = 0
        for s in block.get("steps", []):
            if s.get("type") == "repetition":
                inner_sum += _calculate_block_length(s)
            else:
                inner_sum += s["length"]["value"]
        return rep_count * inner_sum
    else:
        return sum(s["length"]["value"] for s in block.get("steps", []))


def _clean_private_keys(obj: Any) -> None:
    """Recursively remove internal keys (prefixed with '_') before API submission."""
    if isinstance(obj, dict):
        keys_to_remove = [k for k in obj if k.startswith("_")]
        for k in keys_to_remove:
            del obj[k]
        for v in obj.values():
            _clean_private_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            _clean_private_keys(item)


def parse_intervals_to_tp_structure(description_text: str, metric: str = "power") -> List[Dict[str, Any]]:
    """
    Parse 80/20 textual interval syntax into an array of structured step
    dictionaries compatible with TrainingPeaks.

    Handles:
      - Sequential steps separated by commas
      - Repeat loops: 'N x (step1 / step2)'
      - Nested repeat loops recursively
      - Correct TrainingPeaks 'begin' and 'end' timeline offsets

    Returns:
      List of top-level step and repetition blocks for 'structure.structure'.
    """
    cleaned_desc = clean_description_text(description_text)
    items = _split_respecting_parentheses(cleaned_desc, delimiters=",")

    blocks: List[Dict[str, Any]] = []
    current_offset = 0

    has_rep = False
    for i, it in enumerate(items):
        is_first = (i == 0)
        is_last = (i == len(items) - 1)

        block = _parse_block(
            it,
            is_first=is_first,
            is_last=is_last,
            has_preceding_rep=has_rep,
            metric=metric,
        )
        block_len = _calculate_block_length(block)

        block["begin"] = current_offset
        block["end"] = current_offset + block_len
        current_offset += block_len

        if block.get("type") == "repetition":
            has_rep = True
        elif has_rep and block.get("type") == "step":
            # Keep recovery context active if immediately following rep
            pass

        blocks.append(block)

    return blocks


def calculate_estimated_metrics(
    blocks: List[Dict[str, Any]], is_distance: bool
) -> Tuple[Optional[float], Optional[float]]:
    """
    Compute estimated TSS and Intensity Factor (IF) for duration-based workouts
    using standard 80/20 power/pace zone multipliers.
    Returns (tssPlanned, ifPlanned).
    """
    if is_distance:
        return None, None

    zone_times: Dict[int, float] = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}

    def _collect_times(block: Dict[str, Any], multiplier: int = 1) -> None:
        if block.get("type") == "repetition":
            rep_count = block["length"]["value"] * multiplier
            for s in block.get("steps", []):
                if s.get("type") == "repetition":
                    _collect_times(s, rep_count)
                else:
                    z = s.get("_zone", 1)
                    sec = s["length"]["value"]
                    zone_times[z] = zone_times.get(z, 0.0) + (sec * rep_count)
        else:
            for s in block.get("steps", []):
                z = s.get("_zone", 1)
                sec = s["length"]["value"] * multiplier
                zone_times[z] = zone_times.get(z, 0.0) + sec

    for b in blocks:
        _collect_times(b)

    total_sec = sum(zone_times.values())
    if total_sec <= 0:
        return None, None

    total_hours = total_sec / 3600.0
    weighted_sum = sum(
        (ZONE_POWER_MULTIPLIERS.get(z, 0.75) ** 4) * sec
        for z, sec in zone_times.items()
    )
    intensity = (weighted_sum / total_sec) ** 0.25
    tss = round(100.0 * (intensity ** 2) * total_hours, 1)
    if_val = round(intensity, 2)
    return tss, if_val


def build_tp_workout_payload(
    workout: Dict[str, Any],
    library_id: int,
    metric: str = "power",
) -> Dict[str, Any]:
    """
    Assemble the complete TrainingPeaks POST/PUT payload for an exercise library item.
    """
    blocks = parse_intervals_to_tp_structure(workout["description"], metric=metric)

    # Determine if primary metric is distance or duration
    is_distance = (
        workout.get("total_distance") is not None
        or "mile" in workout["description"].lower()
        or " mi " in workout["description"].lower()
    )

    tss, if_val = calculate_estimated_metrics(blocks, is_distance)

    # Clean internal helper keys (_zone) before final assembly
    _clean_private_keys(blocks)

    total_time_hours: Optional[float] = None
    distance_meters: Optional[float] = None

    if is_distance:
        if workout.get("total_distance"):
            distance_meters = float(workout["total_distance"])
        else:
            # Sum up from blocks
            distance_meters = float(blocks[-1]["end"]) if blocks else 0.0
        primary_metric = "distance"
    else:
        if workout.get("total_duration"):
            total_time_hours = round(workout["total_duration"] / 3600.0, 4)
        else:
            total_sec = blocks[-1]["end"] if blocks else 0
            total_time_hours = round(total_sec / 3600.0, 4)
        primary_metric = "duration"

    intensity_metric_str = "percentOfFtp" if metric == "power" else "percentOfThresholdPace"

    structure_dict: Dict[str, Any] = {
        "structure": blocks,
        "primaryLengthMetric": primary_metric,
        "primaryIntensityMetric": intensity_metric_str,
        "primaryIntensityTargetOrRange": "range",
    }
    if is_distance:
        structure_dict["visualizationDistanceUnit"] = "mile"

    payload: Dict[str, Any] = {
        "exerciseLibraryId": int(library_id),
        "exerciseLibraryItemType": "WorkoutTemplate",
        "itemName": workout["code"],
        "workoutTypeId": 3,  # 3 = Running
        "totalTimePlanned": total_time_hours,
        "distancePlanned": distance_meters,
        "caloriesPlanned": None,
        "tssPlanned": tss,
        "ifPlanned": if_val,
        "velocityPlanned": None,
        "energyPlanned": None,
        "elevationGainPlanned": None,
        "description": workout["description"],
        "coachComments": None,
        "fileAttachments": "",
        "structure": structure_dict,
        "fromLegacy": None,
        "workoutSubTypeId": None,
    }
    return payload


# ============================================================================
# 3. API Ingestion Client (TrainingPeaksClient)
# ============================================================================

class TrainingPeaksClient:
    """Client for TrainingPeaks private REST API with rate limiting and exponential backoff."""

    BASE_URL = "https://tpapi.trainingpeaks.com/exerciselibrary/v1"

    def __init__(self, auth_cookie: str, library_id: int):
        self.auth_cookie = auth_cookie.strip('"').strip("'")
        self.library_id = int(library_id)
        self.session = requests.Session()
        self.session.headers.update({
            "Cookie": f"Production_tpAuth={self.auth_cookie}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Origin": "https://app.trainingpeaks.com",
            "Referer": "https://app.trainingpeaks.com/",
        })
        self.last_request_time = 0.0
        self.min_interval = 0.5  # 500ms rate limiting

    def _rate_limit(self) -> None:
        """Enforce at least 500ms between consecutive requests."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request_time = time.time()

    def get_library_items(self) -> Tuple[bool, List[Dict[str, Any]]]:
        """Fetch all existing workout items currently in the exercise library.
        Returns (success, items)."""
        self._rate_limit()
        url = f"{self.BASE_URL}/libraries/{self.library_id}/items"
        logger.info("Fetching existing library items from TrainingPeaks...")
        try:
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return True, data
                elif isinstance(data, dict) and "items" in data:
                    return True, data["items"]
                return True, []
            elif resp.status_code == 401:
                logger.error("Authentication failed (HTTP 401 Unauthorized). Your TP_AUTH_COOKIE has expired.")
                return False, []
            else:
                logger.warning("Failed to fetch library items (status %d): %s", resp.status_code, resp.text[:200])
                return False, []
        except Exception as e:
            logger.warning("Error fetching library items: %s", e)
            return False, []

    def upload_workout(self, payload: Dict[str, Any], max_retries: int = 5) -> Tuple[bool, str]:
        """
        Upload a single workout payload to the exercise library with exponential backoff
        on HTTP 429 and HTTP 5xx errors.
        """
        url = f"{self.BASE_URL}/libraries/{self.library_id}/items"
        backoff_sec = 1.0

        for attempt in range(1, max_retries + 1):
            self._rate_limit()
            try:
                resp = self.session.post(url, json=payload, timeout=30)
                if resp.status_code in (200, 201):
                    item_id = resp.json().get("exerciseLibraryItemId", "N/A")
                    return True, f"Item created (ID: {item_id})"

                if resp.status_code in (429, 500, 502, 503, 504):
                    logger.warning(
                        "HTTP %d on attempt %d/%d for '%s'. Backing off for %.1fs...",
                        resp.status_code, attempt, max_retries, payload.get("itemName"), backoff_sec
                    )
                    time.sleep(backoff_sec)
                    backoff_sec *= 2.0
                    continue

                # Non-retriable error
                return False, f"HTTP {resp.status_code}: {resp.text[:300]}"

            except requests.RequestException as e:
                logger.warning(
                    "Network error on attempt %d/%d for '%s': %s. Retrying in %.1fs...",
                    attempt, max_retries, payload.get("itemName"), e, backoff_sec
                )
                time.sleep(backoff_sec)
                backoff_sec *= 2.0

        return False, f"Failed after {max_retries} attempts."

    def update_workout(self, item_id: int, payload: Dict[str, Any], max_retries: int = 5) -> Tuple[bool, str]:
        """Update an existing workout item in-place via PUT with exponential backoff."""
        url = f"{self.BASE_URL}/libraries/{self.library_id}/items/{item_id}"
        backoff_sec = 1.0

        for attempt in range(1, max_retries + 1):
            self._rate_limit()
            try:
                resp = self.session.put(url, json=payload, timeout=30)
                if resp.status_code in (200, 204):
                    return True, f"Item updated (ID: {item_id})"

                if resp.status_code in (429, 500, 502, 503, 504):
                    logger.warning(
                        "HTTP %d on attempt %d/%d for updating '%s'. Backing off for %.1fs...",
                        resp.status_code, attempt, max_retries, payload.get("itemName"), backoff_sec
                    )
                    time.sleep(backoff_sec)
                    backoff_sec *= 2.0
                    continue

                return False, f"HTTP {resp.status_code}: {resp.text[:300]}"

            except requests.RequestException as e:
                logger.warning(
                    "Network error on attempt %d/%d for updating '%s': %s. Retrying in %.1fs...",
                    attempt, max_retries, payload.get("itemName"), e, backoff_sec
                )
                time.sleep(backoff_sec)
                backoff_sec *= 2.0

        return False, f"Failed after {max_retries} attempts."


# ============================================================================
# 4. Main Controller & Execution Modes
# ============================================================================

def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape missing 80/20 running workouts and sync to TrainingPeaks Workout Library."
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Scrape and generate payloads into preview JSON without uploading (default mode).",
    )
    mode_group.add_argument(
        "--push",
        action="store_true",
        default=False,
        help="Execute upload to TrainingPeaks Exercise Library.",
    )
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="Optional single category filter (e.g., 'RCI', 'RL', 'RT').",
    )
    parser.add_argument(
        "--metric",
        type=str,
        choices=["power", "pace"],
        default="power",
        help="Target intensity metric: 'power' (percentOfFtp) or 'pace' (percentOfThresholdPace). Default: 'power'.",
    )
    default_output = str(DATA_DIR / "parsed_workouts_preview.json")
    default_cache = str(DATA_DIR / "workout_library.html")

    parser.add_argument(
        "--output",
        type=str,
        default=default_output,
        help=f"Output JSON file for generated payloads (default: '{default_output}').",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Force upload/update even if workout already matches target metric in the TP library.",
    )
    parser.add_argument(
        "--source-url",
        type=str,
        default=SOURCE_URL,
        help="80/20 Workout Library URL.",
    )
    parser.add_argument(
        "--html-cache",
        type=str,
        default=default_cache,
        help=f"Local HTML cache file path (default: '{default_cache}').",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_cli_args()

    # Determine execution mode: CLI flag takes precedence, then env var DRY_RUN, default True (dry-run)
    env_dry_run_str = os.getenv("DRY_RUN", "True").strip().lower()
    env_dry_run = env_dry_run_str not in ("false", "0", "no")

    if args.push:
        push_mode = True
    elif args.dry_run:
        push_mode = False
    else:
        push_mode = not env_dry_run
    dry_run = not push_mode

    # Read credentials
    auth_cookie = os.getenv("TP_AUTH_COOKIE", "").strip()
    library_id_str = os.getenv("TP_LIBRARY_ID", "").strip()

    # Determine target categories
    target_cats = TARGET_CATEGORIES
    if args.category:
        cat_key = args.category.upper().strip()
        if cat_key not in TARGET_CATEGORIES:
            logger.error(
                "Invalid category '%s'. Must be one of: %s",
                args.category, ", ".join(TARGET_CATEGORIES.keys())
            )
            sys.exit(1)
        target_cats = {cat_key: TARGET_CATEGORIES[cat_key]}

    target_intensity_metric = "percentOfFtp" if args.metric == "power" else "percentOfThresholdPace"

    print("=" * 70)
    print("  80/20 ENDURANCE -> TRAININGPEAKS WORKOUT LIBRARY SYNC")
    print("=" * 70)
    print(f"Mode              : {'PUSH (Upload/Update TrainingPeaks)' if push_mode else 'DRY RUN (Preview only)'}")
    print(f"Target Metric     : {args.metric.upper()} ({target_intensity_metric})")
    print(f"Categories        : {', '.join(target_cats.keys())}")
    print(f"TP Library ID     : {library_id_str if library_id_str else '[Not Set]'}")
    print(f"TP Auth Cookie    : {'[Present]' if auth_cookie else '[Missing]'}")
    print(f"Preview Output    : {args.output}")
    print("=" * 70)

    # 1. Scrape & parse 80/20 workouts
    scraped_workouts = fetch_8020_workouts(
        url=args.source_url,
        target_categories=target_cats,
        local_cache_file=args.html_cache,
    )

    if not scraped_workouts:
        logger.error("No workouts found matching the specified categories.")
        sys.exit(1)

    # 2. Build structured TrainingPeaks payloads
    dummy_library_id = int(library_id_str) if (library_id_str and library_id_str.isdigit()) else 0
    payloads: List[Dict[str, Any]] = []

    for wkt in scraped_workouts:
        try:
            payload = build_tp_workout_payload(wkt, dummy_library_id, metric=args.metric)
            payloads.append(payload)
        except Exception as e:
            logger.error("Failed to build TP payload for %s: %s", wkt["code"], e)

    logger.info("Successfully generated %d structured workout payloads (%s).", len(payloads), args.metric)

    # Always write preview JSON
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payloads, f, indent=2)
    logger.info("Saved preview payloads to: %s", os.path.abspath(args.output))

    # If dry-run, stop here
    if dry_run:
        print("\n" + "=" * 70)
        print("DRY RUN COMPLETE")
        print(f"Generated {len(payloads)} workout payloads saved to '{args.output}'.")
        print("To push/align these workouts in TrainingPeaks, run with: python sync_8020_to_tp.py --push")
        print("=" * 70)
        return

    # Push Mode: Validate credentials
    if not auth_cookie or not library_id_str:
        logger.error(
            "Missing credentials for --push mode! Please set TP_AUTH_COOKIE and "
            "TP_LIBRARY_ID in your environment or a .env / trainingpeak.env file."
        )
        sys.exit(1)

    library_id = int(library_id_str)
    # Re-stamp correct library_id if it differed
    for p in payloads:
        p["exerciseLibraryId"] = library_id

    client = TrainingPeaksClient(auth_cookie=auth_cookie, library_id=library_id)

    # Fetch existing library items to avoid creating duplicates and detect items to update
    auth_ok, existing_items = client.get_library_items()
    if not auth_ok:
        logger.error(
            "\n" + "=" * 70 + "\n"
            "AUTHENTICATION ERROR (HTTP 401 Unauthorized)\n"
            "Your 'Production_tpAuth' cookie has expired.\n\n"
            "How to fix:\n"
            "1. Log in to https://app.trainingpeaks.com in your web browser.\n"
            "2. Open Developer Tools (F12) -> Application -> Cookies -> https://app.trainingpeaks.com\n"
            "3. Copy the value of 'Production_tpAuth'.\n"
            "4. Paste it into trainingpeak.env or .env as TP_AUTH_COOKIE=\"<new_value>\".\n"
            "5. Re-run: python sync_8020_to_tp.py --push\n" + "=" * 70
        )
        sys.exit(1)

    # Map existing items by name and normalized name
    existing_items_map: Dict[str, Dict[str, Any]] = {}
    for it in existing_items:
        name = it.get("itemName")
        if name:
            existing_items_map[name] = it
            norm_name = re.sub(r"([A-Za-z]+)0+(\d+)", r"\1\2", name)
            existing_items_map[norm_name] = it

    logger.info("Found %d existing items in TrainingPeaks library.", len(existing_items))

    # Execute upload / update
    uploaded_count = 0
    updated_count = 0
    skipped_count = 0
    failed_count = 0

    print(f"\nStarting batch sync (aligning to {args.metric.upper()})...")
    iterator = tqdm(payloads, desc="Syncing workouts", unit="wkt") if tqdm else payloads

    for idx, payload in enumerate(iterator):
        wkt_code = payload["itemName"]
        raw_code = re.sub(r"([A-Za-z]+)0+(\d+)", r"\1\2", wkt_code)

        existing_item = existing_items_map.get(wkt_code) or existing_items_map.get(raw_code)

        if existing_item:
            item_id = existing_item.get("exerciseLibraryItemId")
            curr_metric = (existing_item.get("structure") or {}).get("primaryIntensityMetric")
            
            # If already matches target metric and not forced, skip
            if curr_metric == target_intensity_metric and not args.force:
                if not tqdm:
                    print(f"[{idx+1}/{len(payloads)}] [SKIP] {wkt_code} already aligned with {target_intensity_metric}.")
                skipped_count += 1
                continue

            # Update existing item in-place
            payload["exerciseLibraryItemId"] = item_id
            success, msg = client.update_workout(item_id, payload)
            if success:
                if not tqdm:
                    print(f"[{idx+1}/{len(payloads)}] [UPDATE] {wkt_code} (ID: {item_id}) -> {target_intensity_metric}")
                updated_count += 1
            else:
                if not tqdm:
                    print(f"[{idx+1}/{len(payloads)}] [FAIL] {wkt_code} update failed: {msg}")
                logger.error("Failed to update %s: %s", wkt_code, msg)
                failed_count += 1
                if "401" in msg:
                    logger.error("Authentication expired during update. Aborting.")
                    break
        else:
            # Create new workout item
            success, msg = client.upload_workout(payload)
            if success:
                if not tqdm:
                    print(f"[{idx+1}/{len(payloads)}] [OK] {wkt_code}: {msg}")
                uploaded_count += 1
            else:
                if not tqdm:
                    print(f"[{idx+1}/{len(payloads)}] [FAIL] {wkt_code}: {msg}")
                logger.error("Failed to upload %s: %s", wkt_code, msg)
                failed_count += 1
                if "401" in msg:
                    logger.error("Authentication expired during upload. Aborting.")
                    break

    print("\n" + "=" * 70)
    print("SYNC SUMMARY")
    print("=" * 70)
    print(f"Total Processed       : {len(payloads)}")
    print(f"Successfully Created  : {uploaded_count}")
    print(f"Successfully Updated  : {updated_count}")
    print(f"Skipped (Aligned)     : {skipped_count}")
    print(f"Failed                : {failed_count}")
    print("=" * 70)


if __name__ == "__main__":
    main()
