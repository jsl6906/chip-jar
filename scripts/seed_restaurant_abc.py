"""Seed Restaurant ABC with the visits from the family spreadsheet.

Usage:
    uv run python scripts/seed_restaurant_abc.py --dry-run   # show what would be inserted
    uv run python scripts/seed_restaurant_abc.py --link      # insert, linking each to its Google place

Idempotent: letters already filled for a round are left alone.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, places  # noqa: E402

# (round_no, letter, name, location, rating)
VISITS = [
    (1, "A", "Absolute Noodle", "DC", 4),
    (1, "B", "B too", "DC", 5),
    (1, "C", "Columbia Firehouse", "Old Town", 5),
    (1, "D", "Don Taco", "Old Town", 5),
    (1, "E", "Eammons", "Old Town", 3),
    (1, "F", "Fionas", "Springfield", 4),
    (1, "G", "Ghengis Grill", "Springfield", 2),
    (1, "H", "Hen Quarter", "Old Town", 3),
    (1, "I", "IHOP", "Springfield", 3),
    (1, "J", "Jinya Ramen Bar", "Fairfax", 4),
    (1, "K", "King St. Blues", "Old Town", 3),
    (1, "L", "London Curry House", "Alexandria", 4),
    (1, "M", "Majestic", "Old Town", 2),
    (1, "N", "Namaste", "Old Town", 4),
    (1, "O", "Old House Cosmopolitan", "Old Town", 3),
    (1, "P", "Peacock Cafe", "DC", 4),
    (1, "V", "Vola cafe", "Old Town", 4),
    (2, "K", "Kogia", "Annandale", 5),
]

# Spreadsheet shorthand -> something Google can geocode.
AREA = {"DC": "Washington DC", "Old Town": "Old Town Alexandria VA"}
# Spellings Google doesn't recognise.
SEARCH_AS = {"Eammons": "Eamonn's A Dublin Chipper"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--link", action="store_true", help="look up each restaurant's Google place id")
    args = ap.parse_args()

    db.open_pool()
    try:
        existing = {(v["round_no"], v["letter"]) for v in db.abc_visits()}
        added = 0
        for round_no, letter, name, location, rating in VISITS:
            if (round_no, letter) in existing:
                print(f"skip  {round_no}{letter}  {name} (already filled)")
                continue
            place_id = None
            if args.link:
                found = places.lookup(f"{SEARCH_AS.get(name, name)} {AREA.get(location, location + ' VA')}")
                # Only trust a match that lands on the same letter.
                if found and places.first_letter(found.name) == letter:
                    place_id = found.id
                print(f"      {name!r} -> {found.name + ' · ' + found.address if found else 'no match'}{'' if place_id or not found else '  (ignored)'}")
            v = {"round_no": round_no, "letter": letter, "name": name, "location": location, "rating": rating,
                 "visited_on": None, "notes": None, "place_id": place_id}
            if args.dry_run:
                print(f"would {round_no}{letter}  {name} · {location} · {rating}")
                continue
            ok = db.save_abc_visit(None, v) is not None
            added += ok
            print(f"{'added' if ok else 'FAIL '} {round_no}{letter}  {name}")
        print(f"\n{added} visit(s) added")
    finally:
        db.close_pool()


if __name__ == "__main__":
    main()
